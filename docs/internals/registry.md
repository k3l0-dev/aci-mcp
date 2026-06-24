# Internals: Registry

Three modules under `mcp/registry/` that work together to serve `search_classes` and `get_schema`, and to build APIC filter strings for `query`.

---

## Module map

```mermaid
graph TD
    subgraph registry["mcp/registry/"]
        desc["descriptions.py\nload_descriptions()\nsearch()"]
        schema["schema.py\nload_schema()"]
        filt["filter.py\nbuild_filter()"]
    end

    subgraph data["data/"]
        json["class-descriptions.json\n15k+ entries (in-memory at startup)"]
        schemas_dir["schemas/{version}/*.json\none file per class (lazy, on-disk)"]
    end

    main["main.py\napp_lifespan + query() tool"]

    main -->|"load at startup"| desc
    desc -->|"reads once"| json
    main -->|"passes schemas_dir path to tools"| schema
    schema -->|"reads on demand"| schemas_dir
    main -->|"calls via ApicClient"| filt
```

---

## descriptions.py

### `load_descriptions(path)`

Reads `class-descriptions.json` into memory at startup. Called once — the result is stored in the FastMCP lifespan context and shared across all requests.

```mermaid
flowchart LR
    FILE["class-descriptions.json\n{className: {label, comment}}"]
    FILE -->|"json.loads()"| MEM["in-memory dict\n~15k entries"]
    MEM -->|"lifespan_context['descriptions']"| TOOLS["search_classes()\nquery() validation"]
```

**Error handling:**

- File missing → `DescriptionsLoadError` (server refuses to start)
- Not valid JSON → `DescriptionsLoadError`
- OS permission error → `DescriptionsLoadError`

### `search(keyword, descriptions, limit)`

O(n) linear scan with relevance scoring. For the full algorithm rationale, measured gains, and evolution history see [search-algorithm.md](search-algorithm.md).

**Scoring rules (applied in order):**

```python
score = 0
if keyword in class_name.lower():  score += 3   # class name match
if keyword in label.lower():       score += 2   # label match
if keyword in comment.lower():     score += 1   # comment match

# Fallback: scan prop_labels only when no match above
if score == 0:
    for pl in meta.get("prop_labels", ()):
        if keyword in pl.lower():
            score = 1
            break   # no accumulation across multiple prop_labels

# Rs/Rt relation classes are penalised — internal plumbing, never the primary target
if score > 0 and _RS_RT_RE.match(class_name):
    score -= 3
```

**Edge cases:**

- Empty keyword → returns `[]` immediately (no scan)
- Missing `label`, `comment`, or `prop_labels` → safe default via `.get()`
- Rs/Rt class whose penalised score reaches 0 → excluded from results

---

## schema.py

### `load_schema(class_name, schemas_dir)`

Lazy per-class loader. No in-memory cache — the OS page cache handles repeated reads efficiently.

```mermaid
flowchart TD
    CALL["load_schema('fvBD', schemas_dir)"]
    CALL --> EXISTS{"schemas_dir/fvBD.json\nexists?"}
    EXISTS -->|"no — try versioned subdir"| GLOB["glob schemas_dir/*/fvBD.json"]
    GLOB -->|"not found"| EMPTY["return {}"]
    EXISTS -->|"yes"| READ["read + json.loads()"]
    GLOB -->|"found (first match)"| READ
    READ --> VALIDATE{"file empty?"}
    VALIDATE -->|"yes"| ERR["raise SchemaLoadError"]
    VALIDATE -->|"no"| EXTRACT["extract query-planning fields only"]
    EXTRACT --> NORM["normalise containedBy dict → list\nnormalise relationTo / relationFrom"]
    NORM --> PROPS["properties = sorted(keys of raw properties dict)"]
    PROPS --> RETURN["return dict"]
```

### Extracted fields

Only these fields are kept — heavy fields are discarded to keep tool responses token-efficient:

**Kept:** `identifiedBy`, `rnFormat`, `containedBy`, `dnFormats`, `relationTo`, `relationFrom`, `properties` (names only), `isAbstract`, `isConfigurable`, `className`, `classPkg`, `label`

**Discarded:** `writeAccess`, `events`, `stats`, `faults`, full property metadata (type, validators, etc.)

### containedBy normalisation

In raw jsonmeta, `containedBy` is a dict with class names as keys:

```json
"containedBy": {"fv:Tenant": "", "uni:Infra": ""}
```

`load_schema()` normalises this to a plain list:

```python
["fv:Tenant", "uni:Infra"]
```

### Schema file lookup

The server supports two layouts for `data/schemas/`:

| Layout | Path tried | Used when |
|---|---|---|
| Flat | `schemas_dir/fvBD.json` | Schemas collected without versioning |
| Versioned | `schemas_dir/{version}/fvBD.json` | Default — `aci-collect` stores schemas under `schemas/{apic_version}/` |

`load_schema()` tries the flat path first, then falls back to a glob for the first versioned match.

---

## filter.py

### `build_filter(class_name, filters)`

Builds an APIC `query-target-filter` string from a plain dict. Called by `ApicClient.query_class()`.

```mermaid
flowchart LR
    DICT["{\"name\": \"servers\",\n\"arpFlood\": \"yes\"}"]
    DICT --> VAL["validate class_name and each key\nagainst ^[A-Za-z][A-Za-z0-9]*$"]
    VAL --> PRED["build eq() predicates:\neq(fvBD.name,\"servers\")\neq(fvBD.arpFlood,\"yes\")"]
    PRED --> WRAP{"n predicates?"}
    WRAP -->|"0"| EMPTY_STR["return ''"]
    WRAP -->|"1"| SINGLE["eq(fvBD.name,\"servers\")"]
    WRAP -->|"2+"| AND["and(eq(...),eq(...))"]
```

### Value escaping

`"` and `\` inside filter values are escaped before embedding:

```python
value.replace("\\", "\\\\").replace('"', '\\"')
```

This prevents injection when attribute values contain special characters.

### Identifier validation

Class names and attribute keys are validated against `^[A-Za-z][A-Za-z0-9]*$` before use:

| Input | Result |
|---|---|
| `"fvBD"` | valid |
| `"fv BD"` | `FilterError` (space) |
| `"123abc"` | `FilterError` (starts with digit) |
| `"fvBD; DROP"` | `FilterError` (semicolon) |

An empty `filters` dict returns `""` — the APIC client omits the `query-target-filter` parameter entirely when the string is empty.
