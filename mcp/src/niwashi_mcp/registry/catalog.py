# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Read the ACI object model from niwaki's embedded catalogue.

This is the 2.0 data layer. It replaces reading 15,452 raw jsonmeta files
(1.82 GB on disk, distributed as a 98.8 MB tarball) with one SQLite database
that ships inside the ``niwaki`` dependency — which is what makes the server
installable with ``uvx`` instead of requiring a git checkout.

**This module is the only place in the codebase that knows niwaki exists.** It
reproduces ``registry.schema`` and ``registry.descriptions`` output exactly, so
the swap is invisible to every caller. Keeping the knowledge here is what makes
the migration reviewable and the rollback cheap.

Why the SQL is written by hand
------------------------------
``niwaki.catalog`` exposes ``describe()``, ``search()`` and friends, but not the
fields this server needs: ``rn_format``, ``class_pkg``, ``is_configurable``,
``containedBy``/``contains``, ``relationTo``/``relationFrom``, the ACI
``modelType`` of a property, or its write access. Those live in the database but
have no public accessor yet (requested upstream). Until they land, the queries
are here — confined to this one module, so swapping them for the public API
later is a change of body, not of shape.

Two traps this module exists to avoid
-------------------------------------
1. **The catalogue is bilingual.** Every property carries a ``wire`` name
   (``descr``) and a ``readable`` name (``description``). This server is
   wire-only end to end: a readable name reaches the APIC, is syntactically
   valid, and returns ``[]`` with no error. Measured: 2,207 properties (4.4 % of
   configurable ones) would slip past ``filter.py``'s identifier regex. **No
   readable name is ever read here.** ``prop.wire_name`` is the only source.
2. **Two shapes leak if taken verbatim.** ``enum`` blobs carry a
   ``defaultValue`` marker entry whose ``localName`` is not an APIC-acceptable
   value (present on 90 % of enums), and ``comment_pool`` stores the string
   ``"null"`` as "no comment" (4,463 rows). Both are filtered, exactly as the
   jsonmeta path filtered them.
"""

from __future__ import annotations

import json
import sqlite3
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Any

from niwashi_mcp.exceptions import DescriptionsLoadError

# Scalar keys copied verbatim from the catalogue, in the order the jsonmeta
# reader emitted them. Order is irrelevant to correctness (dicts compare by
# content) but keeping it stable makes a diff between the two paths readable.
_SCALAR_COLUMNS = (
    ("identified_by", "identifiedBy"),
    ("rn_format", "rnFormat"),
    ("is_abstract", "isAbstract"),
    ("is_configurable", "isConfigurable"),
    ("short_name", "className"),
    ("class_pkg", "classPkg"),
)

# Bit layout of `prop.flags`, read from manifest.prop_flags rather than
# hard-coded: niwaki owns this layout and may extend it. Verified at import.
_EXPECTED_FLAGS = (
    "isConfigurable",
    "needsPropDelimiters",
    "createOnly",
    "readWrite",
    "readOnly",
    "isNaming",
    "secure",
    "implicit",
    "mandatory",
)


def catalog_path() -> Path:
    """Absolute path to the catalogue shipped inside the installed niwaki."""
    import niwaki

    return Path(niwaki.__file__).parent / "query" / "_catalog" / "catalog.db"


@lru_cache(maxsize=1)
def _connect() -> sqlite3.Connection:
    """One read-only connection for the process.

    ``immutable=1`` tells SQLite the file cannot change underneath us, which
    skips locking entirely — correct here because the catalogue is a build
    artefact shipped inside a wheel. ``check_same_thread=False`` is required
    because FastMCP serves requests from a thread pool; it is safe under
    SQLite's default serialised threading mode.

    A single connection is deliberate: opening a second one would load a second
    copy of the string pools (26,654 labels + 25,411 comments) into memory.
    """
    path = catalog_path()
    if not path.is_file():
        raise DescriptionsLoadError(
            f"niwaki catalogue not found at {path}. "
            "Reinstall the niwaki package: pip install --force-reinstall niwaki"
        )
    return sqlite3.connect(f"file:{path}?immutable=1", uri=True, check_same_thread=False)


@lru_cache(maxsize=1)
def _flag_bits() -> dict[str, int]:
    """Map flag name to bit value, from the manifest rather than by assumption."""
    row = _connect().execute("SELECT value FROM manifest WHERE key='prop_flags'").fetchone()
    if not row:
        raise DescriptionsLoadError("catalogue manifest has no prop_flags entry")
    bits = {name: 1 << i for i, name in enumerate(row[0].split(","))}
    missing = [f for f in _EXPECTED_FLAGS if f not in bits]
    if missing:
        raise DescriptionsLoadError(
            f"catalogue prop_flags is missing {missing}; niwaki changed the layout"
        )
    return bits


@lru_cache(maxsize=1)
def apic_version() -> str:
    """APIC release the catalogue was generated from, for traceability.

    Worth logging at startup: from 2.0 this is pinned by the niwaki dependency
    rather than chosen by the operator, and it changes on niwaki's schedule.
    """
    row = _connect().execute("SELECT value FROM manifest WHERE key='apic_version'").fetchone()
    return row[0] if row else "unknown"


def _unzip(blob: bytes | None) -> Any:
    return json.loads(zlib.decompress(blob)) if blob else None


@lru_cache(maxsize=4096)
def _pool(table: str, column: str, ident: int | None) -> str | None:
    if ident is None:
        return None
    row = _connect().execute(f"SELECT {column} FROM {table} WHERE id=?", (ident,)).fetchone()
    return row[0] if row else None


def _comment_text(comment_id: int | None) -> str | None:
    """Join a pooled comment, dropping the ``"null"`` sentinel.

    comment_pool stores a JSON list of lines. ``"null"`` is how "no comment" is
    encoded; 4,463 rows carry it. Reproduces the jsonmeta reader exactly,
    including the *first element only* rule used to build the search index —
    see ``descriptions_index``.
    """
    raw = _pool("comment_pool", "text", comment_id)
    if raw is None:
        return None
    try:
        lines = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        lines = [raw]
    if not isinstance(lines, list):
        lines = [lines]
    text = " ".join(c for c in lines if c and c != "null").strip()
    return text or None


def _index_comment(comment_id: int | None) -> str:
    """The search index's comment: **first line only**, whitespace collapsed.

    Deliberately different from :func:`_comment_text`, which joins every line
    for ``get_schema``. The collector took ``comments[0]`` and normalised
    whitespace with ``" ".join(text.split())`` — ACI comments are full of
    double spaces, and without that normalisation 5,701 entries differ from the
    reference file by whitespace alone. Two functions rather than one flag,
    because these are two different contracts that happen to share a source.
    """
    raw = _pool("comment_pool", "text", comment_id)
    if raw is None:
        return ""
    try:
        lines = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        lines = [raw]
    if not isinstance(lines, list):
        lines = [lines]
    return " ".join(lines[0].split()) if lines and lines[0] else ""


def _class_row(class_name: str) -> sqlite3.Row | None:
    cols = ", ".join(c for c, _ in _SCALAR_COLUMNS)
    return _connect().execute(
        f"SELECT id, {cols}, label_id, residual, dn_formats FROM mo WHERE class_name = ?",
        (class_name,),
    ).fetchone()


def class_exists(class_name: str) -> bool:
    """Exact, case-sensitive existence check.

    Case sensitivity used to need defending explicitly: on a case-insensitive
    filesystem ``fvBd.json`` resolves to ``fvBD.json`` and the old reader had to
    re-derive the name to catch it. SQLite's default BINARY collation makes the
    hazard structurally impossible, so the guard is now the storage engine
    rather than a hand-written comparison.
    """
    return _connect().execute(
        "SELECT 1 FROM mo WHERE class_name = ?", (class_name,)
    ).fetchone() is not None


def _project_property(row: sqlite3.Row, bits: dict[str, int]) -> dict[str, Any]:
    """Compact per-property constraints — same contract as the jsonmeta reader.

    Only ``type`` and ``access`` are always present; every other key appears
    solely when the catalogue declares it, to keep the per-property footprint
    minimal in an agent's context.
    """
    (_wire, comment_id, enum_id, base_type_id, model_type_id, default_val, flags) = row
    flags = flags or 0
    detail: dict[str, Any] = {}

    # type — the semantic ACI model type, falling back to the primitive base.
    model_type = _pool("type_pool", "value", model_type_id) or _pool(
        "type_pool", "value", base_type_id
    )
    if model_type:
        detail["type"] = model_type

    # access — collapse the write flags into one mode an agent can act on.
    configurable = bool(flags & bits["isConfigurable"])
    if not configurable or flags & bits["readOnly"]:
        access = "read-only"
    elif flags & bits["createOnly"]:
        access = "create-only"
    elif flags & bits["readWrite"]:
        access = "read-write"
    elif flags & bits["isNaming"]:
        # Naming properties carry no explicit read/write flag: they are set via
        # the DN at creation and are immutable thereafter.
        access = "create-only"
    else:
        access = "read-only"
    detail["access"] = access

    if flags & bits["isNaming"]:
        detail["naming"] = True
    if flags & bits["mandatory"]:
        detail["mandatory"] = True

    if default_val is not None:
        try:
            default = json.loads(default_val)
        except (json.JSONDecodeError, TypeError):
            default = default_val
        if default not in (None, ""):
            detail["default"] = default

    # options — localName of each enum value, minus the "defaultValue" marker
    # (whose localName duplicates the default and is not an accepted value).
    values = _unzip(_pool_blob("enum", enum_id))
    if values:
        seen: set[str] = set()
        options: list[str] = []
        for v in values:
            local = v.get("localName") if isinstance(v, dict) else None
            if local and local != "defaultValue" and local not in seen:
                seen.add(local)
                options.append(local)
        if options:
            detail["options"] = options

    comment = _comment_text(comment_id)
    if comment:
        detail["comment"] = comment

    return detail


@lru_cache(maxsize=8192)
def _pool_blob(table: str, ident: int | None) -> bytes | None:
    if ident is None:
        return None
    row = _connect().execute(f"SELECT content FROM {table} WHERE id=?", (ident,)).fetchone()
    return row[0] if row else None


def load_schema(
    class_name: str,
    include_property_details: bool = False,
    properties_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Structural schema for one ACI class — the 2.0 replacement for the jsonmeta reader.

    Returns exactly the same dict the jsonmeta path returned, or ``{}`` when the
    class is unknown. The empty dict rather than an exception is deliberate and
    load-bearing: an agent recovers from an empty result, not from a traceback.

    Args:
        class_name: Exact ACI class name, e.g. ``"fvBD"``. Case-sensitive.
        include_property_details: Project constraints for every property.
        properties_filter: Project constraints for these properties only,
            preserving the caller's order. Unknown names are skipped silently,
            matching the previous behaviour.
    """
    row = _class_row(class_name)
    if row is None:
        return {}

    class_id = row[0]
    scalars = row[1 : 1 + len(_SCALAR_COLUMNS)]
    label_id, residual, dn_formats = row[-3], row[-2], row[-1]

    # The nine scalar keys are present on every class in the corpus (measured:
    # 1200/1200 sampled files), so they are emitted unconditionally — including
    # when empty. The jsonmeta reader copied `if k in root`, which meant an
    # empty label came through as "" and an empty containedBy as []. Omitting
    # them here instead would be a silent contract change: a caller doing
    # `schema["label"]` would start raising KeyError.
    result: dict[str, Any] = {}
    for (_col, out_key), value in zip(_SCALAR_COLUMNS, scalars, strict=True):
        if out_key == "identifiedBy":
            result[out_key] = json.loads(value) if isinstance(value, str) else (value or [])
        elif out_key in ("isAbstract", "isConfigurable"):
            result[out_key] = bool(value)
        else:
            result[out_key] = value if value is not None else ""

    result["label"] = _pool("label_pool", "text", label_id) or ""

    extra = _unzip(residual) or {}

    # containedBy is a {"pkg:Class": ""} dict in the source — normalise to keys.
    result["containedBy"] = list(extra.get("containedBy") or {})

    # contains is flattened and sorted so an agent can pass the names straight
    # back to get_schema / query without converting colon notation itself.
    contains = extra.get("contains")
    if contains:
        result["contains"] = sorted(k.replace(":", "") for k in contains)

    # relationTo/relationFrom values are plain strings in the catalogue. The
    # dict form is kept for shape compatibility; `cardinality` is empty for all
    # 2,992 entries, exactly as before — the real cardinality lives on the
    # relation class itself.
    rel_to = extra.get("relationTo")
    if rel_to:
        result["relationTo"] = {
            rel: {
                "targetClass": data if isinstance(data, str) else data.get("targetClass", ""),
                "cardinality": "" if isinstance(data, str) else data.get("cardinality", ""),
            }
            for rel, data in rel_to.items()
        }

    rel_from = extra.get("relationFrom")
    if rel_from:
        result["relationFrom"] = {
            rel: {"sourceClass": data if isinstance(data, str) else data.get("sourceClass", "")}
            for rel, data in rel_from.items()
        }

    # dnFormats is stored NULL when empty, but the key exists on all 15,452
    # classes in the source. Always emitted, as [] when absent from storage.
    result["dnFormats"] = _unzip(dn_formats) or []

    prop_rows = _connect().execute(
        "SELECT wire_name, comment_id, enum_id, base_type_id, model_type_id, "
        "default_val, flags FROM prop WHERE class_id = ?",
        (class_id,),
    ).fetchall()

    if prop_rows:
        by_wire = {r[0]: r for r in prop_rows}
        result["properties"] = sorted(by_wire)

        if include_property_details or properties_filter:
            wanted = (
                [n for n in properties_filter if n in by_wire]
                if properties_filter
                else sorted(by_wire)
            )
            bits = _flag_bits()
            result["property_details"] = {
                name: _project_property(by_wire[name], bits) for name in wanted
            }

    return result


# Labels that appear in virtually every ACI class and carry no discriminating
# signal for search. Without this filter every class would match "name", and a
# query for a concept would return all 15,000 of them.
# Copied from the collector's `_GENERIC_PROP_LABELS` — the index must be
# reproduced exactly, not approximated.
_GENERIC_PROP_LABELS: frozenset[str] = frozenset(
    {
        "Name",
        "Description",
        "Annotation",
        "Tag",
        "Owner",
        "Display Name",
        "Managed By",
        "Monitoring policy",
    }
)


def _extract_prop_labels(class_id: int, bits: dict[str, int]) -> list[str]:
    """Human-readable property labels, for the search index.

    This is what lets a functional query reach a class through one of its
    properties — "ARP flooding" finds ``fvBD`` via its ``arpFlood`` label.

    Reproduces the collector's filtering exactly: hidden properties, generic
    cross-class labels, labels of three characters or fewer (``dn``, ``rn``),
    and labels that merely restate the property name are all dropped. Returns a
    list, in schema order — the reference file stores a list, and the search
    tokeniser depends on that shape.
    """
    rows = _connect().execute(
        "SELECT wire_name, label_id, flags FROM prop WHERE class_id = ?",
        (class_id,),
    ).fetchall()
    hidden = bits.get("isHidden", 0)
    labels: list[str] = []
    seen: set[str] = set()
    for wire, label_id, flags in rows:
        if hidden and (flags or 0) & hidden:
            continue
        label = (_pool("label_pool", "text", label_id) or "").strip()
        if not label or len(label) <= 3:
            continue
        if label in _GENERIC_PROP_LABELS:
            continue
        # A label identical to the technical name means no human label exists.
        if label.lower() == wire.lower() or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels


def descriptions_index() -> dict[str, dict[str, Any]]:
    """Rebuild the search index that ``class-descriptions.json`` used to hold.

    Same shape, same content: ``label``, ``comment``, ``prop_labels``,
    ``isConfigurable``, ``isAbstract``. Entries with nothing searchable are
    dropped, which is what produced the historical gap between the 15,452
    classes in the schema collection and the 15,239 in the index.
    """
    bits = _flag_bits()
    out: dict[str, dict[str, Any]] = {}
    rows = _connect().execute(
        "SELECT id, class_name, label_id, comment_id, is_configurable, is_abstract FROM mo"
    ).fetchall()

    for class_id, name, label_id, comment_id, configurable, abstract in rows:
        entry: dict[str, Any] = {}
        label = (_pool("label_pool", "text", label_id) or "").strip()
        if label:
            entry["label"] = label
        comment = _index_comment(comment_id)
        if comment:
            entry["comment"] = comment
        prop_labels = _extract_prop_labels(class_id, bits)
        if prop_labels:
            entry["prop_labels"] = prop_labels
        if configurable:
            entry["isConfigurable"] = True
        if abstract:
            entry["isAbstract"] = True
        # A class with nothing searchable is omitted. This guard is what
        # produced the historical 213-class gap between the schema collection
        # (15,452) and the index (15,239); reproducing it keeps the two
        # collections exactly as they were.
        if entry:
            out[name] = entry
    return out
