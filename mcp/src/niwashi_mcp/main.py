# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""
main.py

Schema-driven FastMCP server for Cisco ACI APIC — v2.1.0.

Architecture
------------
The server exposes a small set of generic tools that let an LLM navigate the
entire ACI object model without any hardcoded class knowledge:

  search_classes  — discover ACI classes by keyword (label + description)
  get_schema      — inspect identifiers, containment, children, relations, and
                    (on demand) per-property constraints for a class
  query           — execute a filtered class query against the APIC
  get_by_dn       — fetch a single object directly by its DN (shortcut path)
  count           — count objects of a class without transferring them

All ACI domain knowledge comes from the catalogue embedded in the `niwaki`
dependency — 15 k+ classes in one SQLite file, no data bundle to download and
no checkout required.

Typical LLM workflow
--------------------
  1. search_classes("bridge domain")
       → learns that fvBD is the relevant class
  2. get_schema("fvBD")
       → sees identifiedBy=["name"], containedBy=["fv:Tenant"],
         relationTo={"fvRsCtx": ...}, properties=[...]
  3. query("fvBD", filters={"name": "servers"}, scope_dn="uni/tn-OT")
       → returns bridge domain objects with all their APIC attributes

Environment variables (read from .env at startup)
--------------------------------------------------
  APIC_HOST        APIC hostname or IP
  APIC_USER        APIC username (default: admin)
  APIC_PASSWORD    APIC password
  APIC_VERIFY_SSL  "true" to verify the APIC TLS certificate (default: false,
                   because an APIC ships self-signed). While false the APIC
                   password is sent to an unauthenticated peer — a warning is
                   logged at startup. Set it true in production.
  MCP_HOST         Interface to bind (default: 127.0.0.1 — loopback only)
  MCP_PORT         HTTP port the server listens on (default: 8000)
  MCP_ALLOW_NO_AUTH  "true" to permit a routable bind with MCP_API_KEYS unset.
                   Refused otherwise: this process holds APIC credentials.
  MCP_API_KEYS     Comma-separated bearer tokens accepted by ApiKeyMiddleware.
                   Unset means the server runs with NO authentication (logged
                   as a warning at startup).  Reloadable at runtime with
                   SIGHUP, without restarting the process.

query() parameters
------------------
  filters              Simple equality filters {attr: value}
  scope_dn             Restrict query to a subtree DN
  limit / page         Pagination (page-size / 0-based page number)
  fetch_all            Walk every page and return the complete matching set
                       in one call, instead of just the first page
  order_by             e.g. "faultInst.severity|desc"
  include_children     Embed direct children inline, e.g. ["fvSubnet","fvRsCtx"]
                       Equivalent to rsp-subtree=children&rsp-subtree-class=X,Y
  filter_expr          Raw APIC filter: wcard, ne, gt, and/or compositions
                       e.g. 'wcard(fvBD.dn,"uni/tn-OT")'
  config_only          Return only user-configurable attributes, dropping the
                       ~40 operational/internal ones
  rsp_subtree_include  Inline subtrees: "faults", "health", "audit-logs",
                       "faults,required", "faults,no-scoped"
  time_range           Log record window: "24h", "1week", "2026-01-01|2026-01-31"
                       Valid for faultRecord, aaaModLR, eventRecord, healthRecord

query() return shape
--------------------
  An envelope, not a bare list:
    {"results": [...], "returned": <int>, "total_available": <int>,
     "truncated": <bool>, "next_page": <int|None>, "complete": <bool>,
     "note": <str|None>}
  A `truncated: true` response must never be read as a maximum, minimum,
  total, or complete list — re-run with fetch_all=True or page to exhaustion
  first. See the FULL-FABRIC AGGREGATION section of the server instructions.
"""

import asyncio
import json
import logging
import os
import signal
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv
from fastmcp import Context, FastMCP
from fastmcp.server.lifespan import lifespan
from mcp.types import ToolAnnotations
from pydantic import BeforeValidator

from niwashi_mcp.apic.client import ApicClient
from niwashi_mcp.exceptions import ConfigurationError, UnknownClassError
from niwashi_mcp.middleware.auth import ApiKeyMiddleware, KeyStore, load_api_keys
from niwashi_mcp.middleware.health import HealthMiddleware
from niwashi_mcp.middleware.oauth import OAuthDiscoveryMiddleware
from niwashi_mcp.registry import catalog
from niwashi_mcp.registry.descriptions import search as desc_search

# ── Paths ─────────────────────────────────────────────────────────────────────
#
# Until 1.x these were derived from ``__file__``, which worked only because the
# server was always run from a git checkout. Once installed as a package,
# ``__file__`` points inside ``site-packages`` and every one of these would
# resolve to a directory the user has never heard of — silently, since a missing
# .env is not an error and a missing schema directory yields empty results.
#
# They are now resolved from the environment first, then from the working
# directory, and only then from the checkout layout. Every lookup is explicit
# and overridable.


BASE_DIR = Path(__file__).resolve().parent


def _checkout_root() -> Path | None:
    """The repository root, when running from a git checkout — else ``None``.

    ``src/niwashi_mcp/`` sits three levels below the repo root, but that
    arithmetic is only meaningful in a checkout. Applied to an installed
    package it walks out of ``site-packages`` and yields a directory that
    happens to exist and means nothing — which is how a wrong path becomes a
    confusing error instead of a clear one. So the layout is *verified*, not
    assumed.
    """
    candidate = BASE_DIR.parent.parent.parent
    looks_right = (candidate / "mcp" / "pyproject.toml").is_file()
    return candidate if looks_right else None


def _first_existing(*candidates: Path | None) -> Path | None:
    for c in candidates:
        if c is not None and c.exists():
            return c
    return None


_CHECKOUT = _checkout_root()


_USER_ENV = Path.home() / ".config" / "niwashi-mcp" / ".env"
ENV_FILE = (
    Path(os.environ["NIWASHI_MCP_ENV_FILE"])
    if os.environ.get("NIWASHI_MCP_ENV_FILE")
    else _first_existing(
        Path.cwd() / ".env",
        _CHECKOUT / ".env" if _CHECKOUT else None,
        _USER_ENV,
    )
    or Path.cwd() / ".env"
)

# ── Schema list bounding ──────────────────────────────────────────────────────

# `dnFormats` and `containedBy` are unbounded in the object model: a class that
# can hang off almost any MO enumerates one entry per possible parent. The seven
# worst offenders are exactly the ones an agent reaches for most — faultInst,
# faultCounts, faultDelegate, healthInst, tagTag, tagAnnotation,
# aaaRbacAnnotation — and get_schema("faultDelegate") serialises to 7.8 MB of
# JSON across 64,313 dnFormats. Handed to an LLM that is roughly 2M tokens for
# one call.
#
# The enumeration carries almost no signal: every faultDelegate DN is
# "{some parent dn}/fd-[{affected}]-fault-{code}", and that suffix is already in
# `rnFormat`. A sample plus the true total says the same thing in 2 KB, so the
# tool returns a sample by default and reports what it withheld.
#
# 99% of classes have ≤ 158 dnFormats and ≤ 40 containedBy entries; at the
# default of 25 the median class (2 dnFormats, 1 parent) is untouched.
_SCHEMA_LIST_SAMPLE = 25

# Ceiling on the opt-in. The full list stays reachable — capped at 500 entries,
# a faultDelegate schema is ~60 KB instead of 7.8 MB, so no value of list_limit
# can put the caller back in the failure mode this bound exists to prevent.
_SCHEMA_LIST_MAX = 500

# Bounded here rather than in `catalog.load_schema` on purpose: the data layer
# stays a faithful projection of the object model (which is what the baseline
# parity tests verify against the 1.x jsonmeta oracle), and the token budget is
# treated as what it is — a presentation concern of the MCP surface.
#
# The note is worded per key because the totals count different things: a class
# with 24,151 dnFormats has 1,895 parents, and saying "parents" under dnFormats
# would hand the agent a wrong number it has no way to check.
_BOUNDED_SCHEMA_LISTS = {
    "dnFormats": (
        "sample of {total} DN patterns. They differ only in the parent prefix and "
        "all end in the same relative name — 'rnFormat' carries it in full."
    ),
    "containedBy": "sample of {total} parent classes.",
}


def _bound_schema_lists(schema: dict[str, Any], limit: int) -> dict[str, Any]:
    """Trim the unbounded schema lists to `limit`, recording what was withheld.

    Mutates and returns `schema` — the dict is freshly built per call by
    `catalog.load_schema`, so there is no shared state to corrupt.

    A truncated list gains a sibling key, e.g. `dnFormatsTruncated`:

        {"returned": 25, "total": 64313,
         "note": "sample of 64313 DN patterns. They differ only in the parent
                  prefix and all end in the same relative name — 'rnFormat'
                  carries it in full. Raise list_limit (max 500) for a larger
                  sample."}

    Lists at or under the limit are left alone and get no marker, so the common
    class is byte-identical to what it was before the bound existed.
    """
    for key, note in _BOUNDED_SCHEMA_LISTS.items():
        values = schema.get(key)
        if not isinstance(values, list) or len(values) <= limit:
            continue
        total = len(values)
        schema[key] = values[:limit]
        schema[f"{key}Truncated"] = {
            "returned": limit,
            "total": total,
            "note": (
                f"{note.format(total=total)} "
                f"Raise list_limit (max {_SCHEMA_LIST_MAX}) for a larger sample."
            ),
        }
    return schema


# ── Tool parameter coercion ───────────────────────────────────────────────────


def _coerce_json_str(v: object) -> object:
    """JSON-decode a string before Pydantic validates it.

    LLMs sometimes JSON-encode list/dict arguments as strings instead of
    sending native JSON arrays/objects.  This runs as a BeforeValidator so
    '["fvSubnet"]' is silently unwrapped to ["fvSubnet"] before type checking.
    If the string is not valid JSON, it is returned unchanged and Pydantic
    will raise the appropriate type error.
    """
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (json.JSONDecodeError, ValueError):
            pass
    return v


# Pydantic types with transparent JSON-string coercion for LLM tool callers.
_JsonList = Annotated[list[str], BeforeValidator(_coerce_json_str)]
_JsonDict = Annotated[dict[str, str], BeforeValidator(_coerce_json_str)]

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("niwashi-mcp")

# ── Lifespan ──────────────────────────────────────────────────────────────────


@lifespan
async def app_lifespan(server: FastMCP):
    """Load registry and connect to the APIC at startup; close on shutdown.

    Yields a context dict available to all tools via ctx.lifespan_context:
      descriptions  — in-memory class descriptions index
      backend       — ApicClient instance
    """
    load_dotenv(ENV_FILE)

    # The search index is rebuilt from niwaki's catalogue rather than read from
    # data/class-descriptions.json. Proven byte-identical to the file it
    # replaces (15,239 entries, no field differing), so search quality is
    # unchanged by construction — the golden-set metrics are asserted as
    # equalities, not floors, in tests/baseline/.
    #
    # Built ONCE here, deliberately. `descriptions.search()` caches its
    # tokenised index keyed on the *identity* of this dict, so rebuilding it
    # per call would silently re-tokenise 15,239 entries on every query and
    # turn a 15 ms search into seconds.
    # Before anything reads it: the catalogue's schema is private to niwaki, so
    # a release is free to restructure it without breaking SemVer. Checked here
    # so a mismatch is a refused startup naming what moved, never a fabric
    # answered from silently empty fields.
    catalog.verify_catalogue()

    descriptions = catalog.descriptions_index()
    logger.info(
        "Registry loaded — %d class descriptions (niwaki catalogue, APIC %s)",
        len(descriptions),
        catalog.apic_version(),
    )

    host = (
        os.environ.get("APIC_HOST", "")
        .removeprefix("https://")
        .removeprefix("http://")
        .strip()
    )
    if not host:
        raise ConfigurationError(
            "APIC_HOST is not set. Add it to .env or export it before starting the server."
        )

    password = os.environ.get("APIC_PASSWORD", "")
    if not password:
        raise ConfigurationError(
            "APIC_PASSWORD is not set. Add it to .env or export it before starting the server."
        )

    user = os.environ.get("APIC_USER", "admin")
    verify_ssl = os.environ.get("APIC_VERIFY_SSL", "false").lower() == "true"
    if not verify_ssl:
        # The default stays false because an APIC ships with a self-signed
        # certificate and demanding verification out of the box would make the
        # server unusable on most fabrics. But silence was wrong: the first
        # thing this process does is POST APIC_USER and APIC_PASSWORD to
        # /api/aaaLogin.json, and without verification it will do so to
        # whatever answers — an ARP or DNS spoof on the management network
        # collects an often admin-capable credential in clear.
        logger.warning(
            "APIC_VERIFY_SSL is not enabled — the TLS certificate of %s is NOT "
            "verified. The APIC password is sent to whatever answers at that "
            "address. Acceptable on an isolated lab; set APIC_VERIFY_SSL=true "
            "in production.",
            host,
        )
    backend = ApicClient(host=host, user=user, password=password, verify_ssl=verify_ssl)
    await backend.authenticate()
    logger.info("Connected to APIC — %s", host)

    try:
        yield {
            "descriptions": descriptions,
            "backend": backend,
        }
    finally:
        await backend.close()
        logger.info("Backend closed")


# ── Server ────────────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="niwashi-mcp",
    lifespan=app_lifespan,
    instructions="""
You are an assistant for querying a Cisco ACI fabric through its APIC REST API.

MANDATORY DISCOVERY WORKFLOW — follow this sequence whenever you do NOT already
hold a verified class name and DN:

  Step 1 — ALWAYS call search_classes(keyword) first.
    Your training knowledge of ACI class names is unreliable: names vary
    across APIC versions and many classes have similar names (fvAEPg, fvCEp,
    fvStCEp…).  Never assume a class name — always verify it.

  Step 2 — ALWAYS call get_schema(class_name) before query().
    The schema tells you which attributes exist on the class (properties),
    which attributes uniquely identify instances (identifiedBy), what the
    parent DN looks like (containedBy → use as scope_dn), and which child
    classes it can hold (contains).  To learn valid values, types, defaults,
    and which properties are read-only before setting or filtering on them,
    call get_schema with properties_filter=[...] (or include_property_details).
    Querying with an attribute that does not exist silently returns nothing.

  Step 3 — Only then call query(class_name, filters, scope_dn).
    Use the "dn" from any result as scope_dn to fetch child objects.

SHORTCUT — skip discovery when you already have an exact DN:
    When you already hold an exact class name AND DN from a previous result or
    from a design, call get_by_dn(dn) directly — no search/schema detour needed.
    Add config_only=True to get just the configurable attributes, or
    include_children=[...] to embed child objects in the same call.

COUNTING — to answer "how many X?" call count(class_name, filters, scope_dn).
    It returns a tally without transferring the objects — far cheaper than
    fetching everything just to measure the result set.  count() only gives
    a scalar tally, never a ranking: "how many subnets in total" is count();
    "which BD has the most subnets" is argmax and needs the actual objects —
    call query(..., fetch_all=True) and aggregate locally over "results".
    If count() or query() cannot run at all — an unknown class name, an
    unreachable object, a malformed filter — that is a failure to answer,
    not an answer of zero.  Never restate a tool error as a count or as
    "0 objects"; state that the question could not be verified, and take
    the corrective step the error points to (e.g. call search_classes()
    with one of the suggested closest matches).

FULL-FABRIC AGGREGATION — a single default query() page is NOT the whole
    fabric.  query() returns an envelope: {"results": [...], "returned",
    "total_available", "truncated", "next_page", "complete", "note"}.  For
    "which X has the most/least Y", "how many X in total", "list all X", or
    "max/min across the fabric" questions, a response with truncated=true
    must never be used to state a maximum, minimum, total, or complete list —
    get the rest first.  Use count() for a pure tally; use
    query(..., fetch_all=True) for ranking, argmax, or an exhaustive list,
    aggregating locally over "results".  total_available is the true size of
    the matching set regardless of how much was fetched; if complete=false
    even after fetch_all=True, the safety cap was hit — narrow the query
    (e.g. by tenant scope_dn) and combine results across narrower calls.

RELATION INTEGRITY — never report what an object points to without reading
    the relation's `state`.
    Relations in ACI are objects, not attributes: an Rs object under the
    source holds the reference.  It records the target that was CONFIGURED,
    and that record outlives the target being deleted or renamed.  So a
    populated `tnFvCtxName` / `tDn` is not evidence that the target exists —
    only `state` is.
      state: formed | missing-target | invalid-target |
             cardinality-violation | unformed
      stateQual: none | default-target | mismatch-target
    `missing-target`, `invalid-target` and `cardinality-violation` are
    definite failures — the APIC tried and could not resolve.  Report the
    relation as unresolved, and do NOT fetch the configured DN and present
    the result as the object's target: a missing-target DN can still answer
    get_by_dn() with a live object.
    `unformed` is ambiguous, not a fault on its own — it is the property's
    default and the resting state of many internal relations, most of which
    have targets that do exist.  Report it as "not resolved" and check
    whether the target exists before calling it broken.
    `formed` + `default-target` means it resolved to an INHERITED default
    policy, not to a configured choice; say so or omit it.
    A relation with no `state` at all (an Rt object, or a config_only
    response) is unknown, never healthy.
    Two measured traps: `state`/`stateQual` are not filterable — a
    filter_expr against them returns zero results without erroring, in both
    directions — and a fabric-wide sweep of relation classes silently
    returns a fraction of the real population.  Inspect relations per object
    or per tenant and filter locally.
    Read them with include_children (query) or get_by_dn(dn,
    include_children=[...]).  The APIC also materialises the reverse
    direction: an Rt object under the TARGET, one per referring source, each
    carrying that source's DN in `tDn` — that is the equivalent of the APIC
    UI's "Show Usage", and it is how you answer "what would break if I
    deleted this?".

CLEAN CONFIG — pass config_only=True to query() or get_by_dn() to drop the ~40
    operational/internal attributes and keep only the intended configuration,
    ideal for comparison, drift detection, and backup.

GROUNDING — every specific fact in your final answer (a property name, a
    configured value, a DN template, a relation target, a count) must come
    from a tool result you actually received in this conversation.  Do not
    complete a partial answer from general ACI knowledge — only the
    fabric's own response is authoritative for a specific deployment.
    Two default get_schema() fields are easy to over-read: contains lists
    child CLASS NAMES only, never what each one targets or means; and
    properties lists NAMES only, never types/defaults/allowed values,
    unless you asked for property_details.  Naming a relation's target or a
    property's type/value without having actually requested that detail is
    exactly the kind of unsupported completion this rule forbids.

Skipping discovery (steps 1-2) for an UNVERIFIED class produces wrong class
names, wrong filters, and empty results.  The search + schema cost is two fast
local lookups — always worth it when you are not already certain.
""",
)

# ── Tools ─────────────────────────────────────────────────────────────────────

# Every tool here is READ-ONLY. `search_classes` and `get_schema` never leave the
# process; `query`, `get_by_dn` and `count` issue GETs against the APIC and
# nothing else — ApicClient has no POST path but the login. Saying so in the
# tool annotations is not decoration: a client that does not know a tool is safe
# has to assume it is not, and prompts the user on every single call. An agent
# answering one question makes a dozen of these calls.
#
# Only `readOnlyHint` and `openWorldHint` are set. Per the MCP specification,
# `destructiveHint` and `idempotentHint` are meaningful only when `readOnlyHint`
# is false, so declaring them here would be noise that reads as rigour.
_LOCAL_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_FABRIC_READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


@mcp.tool(title="Search ACI classes", annotations=_LOCAL_READ)
async def search_classes(
    keyword: str,
    ctx: Context,
    limit: int = 10,
) -> list[dict[str, str]]:
    """Search ACI class descriptions by keyword.

    Tokenizes the keyword and each class's name/label/comment/property
    labels (camelCase-aware), then scores by weighted signal — exact
    label/jargon match, squashed class-name match, token coverage, a small
    curated synonym table — plus structural priors applied after the text
    score (configurable/abstract/stats-suffix/relation-class). See
    docs/internals/search-algorithm.md for the full algorithm.

    Use this tool whenever the exact ACI class name for a concept is not
    known (e.g. "bridge domain", "contract", "fault", "VRF", "node").

    Args:
        keyword: Plain English term or partial ACI class name to search for.
        ctx:     Injected FastMCP context (not a user-facing parameter).
        limit:   Maximum results to return (default 10). Clamped to the
                 range [1, 50] — values below 1 (including 0 and negatives)
                 are raised to 1 rather than passed through, since a
                 non-positive limit would otherwise silently mis-slice the
                 results list instead of returning a sane minimal result set.

    Returns:
        List of dicts, each with:
          class_name — ACI class name, e.g. "fvBD"
          label      — short human label, e.g. "Bridge Domain"
          comment    — one-sentence description from the APIC schema
    """
    descriptions: dict = ctx.lifespan_context["descriptions"]
    clamped_limit = max(1, min(limit, 50))
    results = desc_search(keyword, descriptions, clamped_limit)
    await ctx.info(f"search_classes({keyword!r}) → {len(results)} results")
    return results


@mcp.tool(title="Inspect an ACI class schema", annotations=_LOCAL_READ)
async def get_schema(
    class_name: str,
    ctx: Context,
    include_property_details: bool = False,
    properties_filter: _JsonList | None = None,
    list_limit: int = _SCHEMA_LIST_SAMPLE,
) -> dict[str, Any]:
    """Return the structural schema for an ACI class.

    Reads the query-planning fields for the class from the ACI object-model
    catalogue that ships inside the `niwaki` dependency — one SQLite file, no
    directory to point at and nothing to download.  The returned dict contains:

      identifiedBy   — attribute(s) that uniquely identify an instance;
                        use these as filter keys in query()
      rnFormat       — relative-name template showing identifier placeholders
      containedBy    — parent class name(s) in "pkg:Class" notation;
                        fetch the parent object and use its dn as scope_dn.
                        Sampled to list_limit entries on classes that attach
                        to many parents — see dnFormats below.
      contains       — sorted list of child class names this object may hold,
                        in flat notation (e.g. "fvSubnet", "tagTag") ready to
                        pass to get_schema(), query(), or include_children
      dnFormats      — DN pattern examples for this class.  Universal classes
                        (faultInst, healthInst, tagTag, faultDelegate …) attach
                        to thousands of parents and enumerate one pattern each,
                        so this list is sampled to list_limit entries.  When
                        that happens a `dnFormatsTruncated` key appears
                        alongside it, carrying {returned, total, note}; the same
                        applies to `containedByTruncated`.  The sample loses
                        nothing actionable — every pattern ends in the same
                        suffix, which is what `rnFormat` already gives you.
      relationTo     — outgoing Rs relations: {relClass: {targetClass, cardinality}}
                        Keys and targetClass keep their "pkg:Class" colon
                        notation — unlike `contains`, they are NOT flattened,
                        so strip the colon before querying.  `cardinality` is
                        empty for every entry on the current schema bundle;
                        the real value lives on the relation class itself.
      relationFrom   — incoming Rt relations: {relClass: {sourceClass}}, also
                        in colon notation
      properties     — sorted list of all available attribute names
      property_details — compact per-property constraints; present ONLY when
                        include_property_details=True or properties_filter is set
      isAbstract     — True when the class cannot be directly instantiated
      isConfigurable — True when objects can be created/modified via APIC
      className      — short name without package prefix, e.g. "BD"
      classPkg       — package prefix, e.g. "fv"
      label          — human-readable label

    Property details are opt-in to protect the token budget — many classes carry
    100+ properties.  Request them only for the properties you intend to set or
    filter on.  Each entry in property_details has the shape:

      {"type": <ACI model type>,          # e.g. "scalar:Bool", "fv:RouteScp"
       "access": "read-write"|"create-only"|"read-only",
       "naming": true,                    # only when the property is part of the DN
       "default": <value>,                # only when the schema declares one
       "options": [<allowed value>, ...], # only for enumerated properties
       "comment": <one-line description>}

    Args:
        class_name: Exact ACI class name, e.g. "fvBD", "fvAEPg", "faultInst".
        ctx:        Injected FastMCP context (not a user-facing parameter).
        include_property_details: When True, include property_details for EVERY
                    property.  Prefer properties_filter unless you truly need all.
        properties_filter: Names of the properties to include in property_details.
                    This is the token-efficient path — ask only for the properties
                    you care about.  Unknown names are silently skipped.
        list_limit: How many `dnFormats` and `containedBy` entries to return
                    (default 25, clamped to 1..500).  Only the handful of
                    universal classes ever hit it; leave it alone unless a
                    `*Truncated` marker tells you the sample was too small.

    Returns:
        Schema dict as described above, or an empty dict when the catalogue
        holds no such class.  Lookup is exact and case-sensitive: `fvBd` does
        not resolve to `fvBD`.

    Raises:
        DescriptionsLoadError: The niwaki catalogue is missing or unreadable.
                         Indicates a broken installation, not a missing class
                         (a missing class returns {} instead, see Returns
                         above). Reinstall with:
                         pip install --force-reinstall niwaki
    """
    schema = catalog.load_schema(
        class_name,
        include_property_details=include_property_details,
        properties_filter=properties_filter,
    )
    if schema:
        _bound_schema_lists(schema, max(1, min(list_limit, _SCHEMA_LIST_MAX)))
        await ctx.info(f"get_schema({class_name!r}) → OK")
    else:
        await ctx.warning(f"get_schema({class_name!r}) → not found")
    return schema


@mcp.tool(title="Query ACI objects", annotations=_FABRIC_READ)
async def query(
    class_name: str,
    ctx: Context,
    filters: _JsonDict | None = None,
    scope_dn: str | None = None,
    limit: int = 20,
    order_by: str | None = None,
    include_children: _JsonList | None = None,
    filter_expr: str | None = None,
    rsp_subtree_include: str | None = None,
    time_range: str | None = None,
    page: int | None = None,
    config_only: bool = False,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """Query ACI objects of a given class from the APIC.

    ⚠ PREREQUISITE — before calling this tool you MUST have:
      1. Called search_classes(keyword) to verify the exact class name.
      2. Called get_schema(class_name) to learn valid filter attribute names.
    Skipping these steps leads to empty results with no error — the APIC
    silently returns nothing for unknown classes or wrong attribute names.

    Executes a GET against the APIC class or subtree endpoint.
    The APIC filter string is built automatically from `filters`.
    Providing `scope_dn` issues a subtree query rooted at that DN,
    which is faster and more precise than a fabric-wide class scan.

    ⚠ A single page is NOT the whole fabric. `limit` caps how many objects
    come back in one call — a class can have far more matches than that.
    Check the returned `truncated` field before treating a default-page
    result as a maximum, minimum, total, or complete list. See `fetch_all`
    below, and the FULL-FABRIC AGGREGATION section of these instructions.

    Args:
        class_name: Exact ACI class name verified via search_classes(),
                    e.g. "fvBD", "faultInst", "fabricNode".
        ctx:        Injected FastMCP context (not a user-facing parameter).
        filters:    Attribute filters as {attribute: value} pairs.
                    Attribute names must match properties from get_schema().
                    Example: {"name": "servers"}  →  eq(fvBD.name,"servers")
                    Multiple entries are combined with APIC and() syntax.
        scope_dn:   DN of a parent object to scope the subtree query.
                    Example: "uni/tn-OT" restricts results to tenant OT.
                    Use the "dn" field from a previous query result.
        limit:            Maximum objects to return per page (default 20).
                          Clamped to the range [1, 200] — values below 1
                          (including 0 and negatives) are raised to 1 rather
                          than passed through to the APIC as an invalid
                          page-size. Also the page size used internally when
                          fetch_all=True.
        order_by:         APIC ordering expression, e.g. "faultInst.severity|desc".
        include_children: Child class names to embed in each result in one call,
                          e.g. ["fvSubnet", "fvRsCtx"].  Each returned object
                          will contain a "_children" list of child attribute dicts.
                          Equivalent to moquery -x rsp-subtree=children
                          -x rsp-subtree-class=X,Y.
        filter_expr:      Raw APIC filter predicate for operations beyond simple
                          equality (wcard, ne, gt, ...), e.g.
                          'wcard(fvBD.dn,"uni/tn-OT")'. Combined with `filters`
                          via and() when both are provided.
        rsp_subtree_include: Inline subtree categories to include in the same
                          response, e.g. "faults", "health", "audit-logs",
                          "faults,no-scoped", "faults,required".
        time_range:       Time window for log-record classes, e.g. "24h",
                          "1week", "2026-01-01|2026-01-31". Valid for
                          faultRecord, aaaModLR, eventRecord, healthRecord.
        page:             Page number for explicit manual pagination (0-based).
                          Ignored when fetch_all=True.
        config_only:      When True, return only user-configurable attributes
                          (APIC rsp-prop-include=config-only) instead of the full
                          ~40-attribute set.  Use it when comparing, backing up,
                          or diffing intended configuration without operational
                          noise (runtime state, timestamps, monitoring counters).
        fetch_all:        When True, walk every page (using `limit` as page
                          size) and return the complete matching set in one
                          call — the reliable way to answer a max/min/total/
                          all-of question over a whole class instead of
                          paging manually. Stops early only if a safety cap
                          (thousands of objects) is hit, in which case
                          `complete` is False in the response; narrow the
                          query (e.g. scope_dn) and combine results.

    Returns:
        An envelope dict:
          {"results": [...],            # attribute dicts, same shape as before
           "returned": <int>,           # len(results)
           "total_available": <int>,    # true match count, fabric/subtree-wide
           "truncated": <bool>,         # total_available > returned
           "next_page": <int|None>,     # page+1 when truncated and not fetch_all
           "complete": <bool>,          # False only if fetch_all hit the safety cap
           "note": <str|None>}          # guidance, present only when truncated or capped
        Each dict in "results" contains all APIC attributes for the object
        plus a "_class" key with the ACI class name. The "dn" attribute is
        always present and encodes the full object path. When
        include_children is set, each dict also contains "_children": a list
        of child attribute dicts, each with their own "_class" key.

    Raises:
        UnknownClassError: class_name is not in the catalogue — includes the
                           closest matches so the caller can self-correct
                           without another search_classes() round-trip. The
                           check is exact and case-sensitive. It exists because
                           the APIC answers an unknown class with an empty
                           result rather than an error, which is
                           indistinguishable from "there are none".
        FilterError:       `class_name` or a `filters` key contains characters
                           outside the expected ACI identifier format (see
                           registry.filter.build_filter). Filter *values* are
                           always escaped, never rejected — this can only be
                           raised by an identifier, not a value.
        ApicRequestError:  APIC returned a non-2xx, non-auth response — e.g.
                           400 for a malformed filter_expr, or 500. Carries
                           the HTTP status and, when present, the APIC error
                           text.
    """
    descriptions: dict = ctx.lifespan_context["descriptions"]
    backend: ApicClient = ctx.lifespan_context["backend"]
    # Validate class_name before hitting the backend, which would silently
    # return [] for a typo. One lookup against the catalogue, which is now the
    # single source of truth for "does this class exist".
    #
    # This used to be two tiers: the descriptions index, then a fallback to the
    # schema files, because the two collections disagreed by 213 classes and a
    # class missing from the first could still be perfectly queryable. Both now
    # come from the same catalogue, so the fallback — and the warning it emitted
    # on 213 valid classes — is gone.
    if not catalog.class_exists(class_name):
        suggestions = desc_search(class_name, descriptions, limit=5)
        suggestion_names = [s["class_name"] for s in suggestions]
        await ctx.warning(f"query called with unknown class {class_name!r}")
        raise UnknownClassError(class_name, suggestion_names, len(descriptions))

    clamped_limit = max(1, min(limit, 200))
    await ctx.info(
        f"query({class_name!r}, filters={filters!r}, scope={scope_dn!r}, "
        f"limit={clamped_limit}, fetch_all={fetch_all})"
    )

    result = await backend.query_class(
        class_name=class_name,
        filters=filters or {},
        scope_dn=scope_dn or "",
        limit=clamped_limit,
        order_by=order_by or "",
        include_children=include_children,
        filter_expr=filter_expr,
        rsp_subtree_include=rsp_subtree_include,
        time_range=time_range,
        page=page,
        config_only=config_only,
        fetch_all=fetch_all,
    )

    returned = len(result.objects)

    # `truncated` must be measured against what the caller has *consumed*, not
    # against the size of the page in hand. Comparing `total_available` to
    # `returned` alone made it permanently true: page 2 of 45 objects returned
    # 5 and still reported truncated, as did page 99 returning 0. Since
    # SKILL.md and docs/tools/query.md both instruct an agent to "page until
    # truncated is false", an agent following the documented procedure looped
    # until it exhausted its turn budget, spending one APIC call per iteration
    # and returning nothing.
    #
    # fetch_all walks every page itself, so everything is already in hand.
    consumed = 0 if fetch_all else (page or 0) * clamped_limit + returned
    truncated = False if fetch_all else consumed < result.total_available

    note: str | None = None
    if truncated and not fetch_all:
        note = (
            f"Only {returned} of {result.total_available} results returned — "
            "this is a partial page. Do not conclude a maximum, minimum, "
            "total, or complete list from it. Re-run with fetch_all=True to "
            "get everything, or page explicitly."
        )
        await ctx.warning(
            f"query({class_name!r}) truncated — {returned}/{result.total_available} "
            "returned; re-run with fetch_all=True or page explicitly"
        )
    elif not result.complete:
        note = (
            f"Fetched {returned} of {result.total_available} objects before "
            "hitting the safety cap. Narrow the query (e.g. scope_dn) and "
            "combine results."
        )
        await ctx.warning(
            f"query({class_name!r}, fetch_all=True) capped — "
            f"{returned}/{result.total_available} fetched"
        )

    await ctx.info(
        f"query → {returned}/{result.total_available} objects returned "
        f"(truncated={truncated}, complete={result.complete})"
    )

    return {
        "results": result.objects,
        "returned": returned,
        "total_available": result.total_available,
        "truncated": truncated,
        "next_page": (page or 0) + 1 if truncated and not fetch_all else None,
        "complete": result.complete,
        "note": note,
    }


@mcp.tool(title="Fetch one ACI object by DN", annotations=_FABRIC_READ)
async def get_by_dn(
    dn: str,
    ctx: Context,
    config_only: bool = False,
    include_children: _JsonList | None = None,
) -> dict[str, Any]:
    """Fetch a single ACI object directly by its Distinguished Name.

    SHORTCUT — this is the fast path that skips the search_classes → get_schema →
    query discovery sequence.  Use it whenever you ALREADY hold an exact DN from
    a previous result or from a design; there is no need to know the class name
    up front, since the DN encodes it.  For an UNKNOWN object, use the discovery
    workflow (search_classes → get_schema → query) instead.

    Issues GET /api/mo/{dn}.json against the APIC.

    Args:
        dn:               Full Distinguished Name, e.g. "uni/tn-OT/BD-servers".
        ctx:              Injected FastMCP context (not a user-facing parameter).
        config_only:      When True, return only user-configurable attributes
                          (APIC rsp-prop-include=config-only) — ideal for backup
                          and comparison without operational noise.
        include_children: Child class names to embed, e.g. ["fvSubnet", "fvRsCtx"].
                          The returned object gains a "_children" list.

    Returns:
        On success, the object's attribute dict — same shape as a query() result
        element: all APIC attributes plus a "_class" key (and "_children" when
        include_children is set).

        When no object exists at that DN, a structured not-found dict:
          {"found": False, "dn": <dn>, "message": <explanation>}
        The APIC returns an empty result for a missing DN; this makes that
        explicit so you do not mistake it for a silent failure.  A typical cause
        is a stale or mistyped DN — re-verify it via search_classes → query.

    Raises:
        ApicRequestError: APIC returned a non-2xx, non-auth response — e.g. 400
                          for a malformed DN string. Carries the HTTP status
                          and, when present, the APIC error text.
    """
    backend: ApicClient = ctx.lifespan_context["backend"]

    await ctx.info(f"get_by_dn({dn!r}, config_only={config_only})")
    obj = await backend.get_by_dn(
        dn=dn,
        config_only=config_only,
        include_children=include_children,
    )
    if obj is None:
        await ctx.warning(f"get_by_dn({dn!r}) → not found")
        return {
            "found": False,
            "dn": dn,
            "message": (
                f"No object exists at DN '{dn}'. The DN may be mistyped, or the "
                "object may have been deleted. Verify it with search_classes() "
                "and query(), or re-derive the DN from a fresh query result."
            ),
        }
    await ctx.info(f"get_by_dn({dn!r}) → {obj.get('_class')}")
    return obj


@mcp.tool(title="Count ACI objects", annotations=_FABRIC_READ)
async def count(
    class_name: str,
    ctx: Context,
    filters: _JsonDict | None = None,
    scope_dn: str | None = None,
    filter_expr: str | None = None,
) -> dict[str, Any]:
    """Count ACI objects of a class without transferring them.

    Answers "how many X?" — a pure tally. It does NOT answer "which X has the
    most/least Y?" (ranking/argmax): that needs the actual objects, so use
    query(..., fetch_all=True) plus local aggregation instead.

    ⚠ PREREQUISITE — like query(), verify the class name with search_classes()
    (and its filter attributes with get_schema()) before calling this tool.

    The most frequent verification need — "how many BDs / EPGs / subnets?" —
    answered in a single cheap request that transfers one object instead of the
    whole matching set. Filtering and scoping behave exactly as in query(), and
    the tally is the same `total_available` query() reports, so the two tools
    can never disagree about the size of the same result set.

    Args:
        class_name:  Exact ACI class name verified via search_classes().
        ctx:         Injected FastMCP context (not a user-facing parameter).
        filters:     Attribute equality filters {attribute: value}, same as query().
        scope_dn:    DN of a parent object to scope the count to a subtree.
        filter_expr: Raw APIC filter string for predicates beyond equality;
                     combined with `filters` via and() when both are provided.

    Returns:
        A dict:
          {"class_name": <class_name>,
           "count": <int>,
           "scope_dn": <scope_dn or None>,
           "filters": <filters or {}>}

    Raises:
        UnknownClassError: class_name is not in the catalogue — the same
                           guard query() applies, from the same single source
                           of truth, so the two tools can never disagree about
                           whether a class exists.
        FilterError:       `class_name` or a `filters` key contains characters
                           outside the expected ACI identifier format (see
                           registry.filter.build_filter). Filter values are
                           always escaped, never rejected.
        ApicRequestError:  APIC returned a non-2xx, non-auth response — e.g.
                           400 for a malformed filter_expr, or 500. Carries
                           the HTTP status and, when present, the APIC error
                           text.
    """
    descriptions: dict = ctx.lifespan_context["descriptions"]
    backend: ApicClient = ctx.lifespan_context["backend"]
    # Identical guard to query(), so the two tools can never disagree on
    # whether a class is "known".
    if not catalog.class_exists(class_name):
        suggestions = desc_search(class_name, descriptions, limit=5)
        suggestion_names = [s["class_name"] for s in suggestions]
        await ctx.warning(f"count called with unknown class {class_name!r}")
        raise UnknownClassError(class_name, suggestion_names, len(descriptions))

    await ctx.info(
        f"count({class_name!r}, filters={filters!r}, scope={scope_dn!r})"
    )
    total = await backend.count_class(
        class_name=class_name,
        filters=filters or {},
        scope_dn=scope_dn or "",
        filter_expr=filter_expr,
    )
    await ctx.info(f"count → {total}")
    return {
        "class_name": class_name,
        "count": total,
        "scope_dn": scope_dn,
        "filters": filters or {},
    }


# ── Entry point ───────────────────────────────────────────────────────────────


def _is_loopback(host: str) -> bool:
    """Whether binding to ``host`` keeps the server off the network.

    ``0.0.0.0`` and ``::`` are wildcards: they bind every interface, so they
    are never loopback however local the machine feels. Anything unparseable
    is treated as routable — the safe reading when in doubt.
    """
    import ipaddress

    if host in ("0.0.0.0", "::", ""):
        return False
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def _serve() -> None:
    load_dotenv(ENV_FILE)
    _port_raw = os.environ.get("MCP_PORT", "8000")
    try:
        port = int(_port_raw)
    except ValueError:
        raise ConfigurationError(
            f"MCP_PORT must be an integer, got '{_port_raw}'."
        ) from None

    # Loopback by default. Until 2.0 this was a hardcoded 0.0.0.0 with no way
    # to change it, while README.md told the reader the server listened on
    # localhost — so the documented quickstart put an unauthenticated server
    # holding APIC credentials on every interface of the machine.
    bind_host = os.environ.get("MCP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    allow_no_auth = os.environ.get("MCP_ALLOW_NO_AUTH", "").lower() == "true"

    from starlette.middleware import Middleware

    # An empty key set disables authentication outright. That is intended on
    # loopback, and refused below on a routable bind — but the refusal only
    # guards startup. Handing the same fact to the KeyStore is what stops a
    # later SIGHUP from re-creating the refused combination behind the guard's
    # back, with the process still running and still reporting healthy.
    auth_optional = _is_loopback(bind_host) or allow_no_auth
    key_store = KeyStore(load_api_keys(), auth_required=not auth_optional)
    if key_store:
        logger.info("API key authentication enabled (%d key(s) loaded)", len(key_store))
    elif auth_optional:
        logger.warning(
            "MCP_API_KEYS is not set — running WITHOUT authentication on %s. "
            "Acceptable on loopback; set MCP_API_KEYS before exposing this server.",
            bind_host,
        )
        if not _is_loopback(bind_host):
            logger.warning(
                "MCP_ALLOW_NO_AUTH=true — the unauthenticated bind on %s is deliberate. "
                "Every tool, and the APIC credentials behind them, are reachable "
                "from the network.",
                bind_host,
            )
    else:
        # Refusing here rather than warning is deliberate. This process holds
        # APIC credentials, usually for an admin-capable account, and an
        # unauthenticated bind on a routable interface hands every tool to
        # anyone on the network — no header required. A log line is not enough
        # of a guard for that: warnings scroll past, and the default path
        # (`uvx niwashi-mcp`) is exactly the one a first-time user takes.
        raise ConfigurationError(
            f"Refusing to listen on {bind_host} without authentication.\n"
            "This server holds APIC credentials; binding a routable interface "
            "with MCP_API_KEYS unset exposes every tool to the network.\n"
            "Choose one:\n"
            "  - set MCP_API_KEYS (recommended), or\n"
            "  - keep the default MCP_HOST=127.0.0.1, or\n"
            "  - set MCP_ALLOW_NO_AUTH=true to accept the risk explicitly."
        )

    # SIGHUP reloads MCP_API_KEYS from .env without restarting the server.
    # Send with: kill -HUP <pid>  or  kill -HUP $(cat .lab.pid)
    def _handle_sighup(_signum, _frame):
        load_dotenv(ENV_FILE, override=True)
        new_keys = load_api_keys()
        if not key_store.reload(new_keys):
            # Refused, not applied: this bind requires authentication and the
            # reload produced nothing to authenticate against. A truncated
            # .env, a file caught mid-rotation, an unmounted secret volume or a
            # mistyped key all land here, and applying any of them would strip
            # authentication from every tool while the server kept serving.
            logger.error(
                "SIGHUP — MCP_API_KEYS is empty after reload; REFUSED, keeping the "
                "previous %d key(s). Applying it would have disabled authentication "
                "on %s, which is routable. Fix the file and send SIGHUP again, or "
                "set MCP_ALLOW_NO_AUTH=true and restart if that is truly intended.",
                len(key_store),
                bind_host,
            )
            return
        n = len(new_keys)
        if n:
            logger.info("SIGHUP — API keys reloaded (%d key(s))", n)
        else:
            logger.warning("SIGHUP — MCP_API_KEYS is empty after reload, auth disabled")

    signal.signal(signal.SIGHUP, _handle_sighup)

    # Middleware order: outermost first. HealthMiddleware must be first so
    # /health is served without auth. OAuthDiscoveryMiddleware must precede
    # ApiKeyMiddleware so /.well-known/ paths are never blocked by auth.
    middleware = [
        Middleware(HealthMiddleware),
        Middleware(OAuthDiscoveryMiddleware),
        Middleware(ApiKeyMiddleware, key_store=key_store),
    ]

    await mcp.run_http_async(
        host=bind_host,
        port=port,
        stateless_http=True,
        json_response=True,
        middleware=middleware,
    )


def main() -> None:
    """Console entry point (``niwashi-mcp``).

    A synchronous wrapper is required because ``[project.scripts]`` cannot
    target a coroutine. Kept deliberately thin so ``_serve`` stays the single
    place where server behaviour lives.
    """
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
