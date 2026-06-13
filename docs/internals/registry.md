# Internals: Registry

Three modules under `mcp/registry/` that work together to serve `search_classes` and `get_schema`, and to build APIC filter strings for `query`.

---

## Module map

```mermaid
graph TD
    subgraph registry["mcp/registry/"]
        desc["descriptions.py<br/>load_descriptions()<br/>search()"]
        schema["schema.py<br/>load_schema()"]
        filt["filter.py<br/>build_filter()"]
    end

    subgraph data["data/"]
        json["class-descriptions.json<br/>15k+ entries (in-memory at startup)"]
        schemas_dir["schemas/*.json<br/>one file per class (lazy, on-disk)"]
    end

    main["main.py\napp_lifespan"]

    main -->|"load at startup"| desc
    desc -->|"reads once"| json
    main -->|"tools use path"| schema
    schema -->|"reads on demand"| schemas_dir
    filt -->|"called by query()"| main
```

---

## descriptions.py

### `load_descriptions(path)`

Reads `class-descriptions.json` into a Python dict at startup. Called once — the result is stored in the lifespan context and shared across all requests.

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

O(n) linear scan with relevance scoring. On 15k classes, a single search completes in < 5 ms (verified by perf tests).

```python
score = 0
if keyword in class_name.lower():  score += 3
if keyword in label.lower():       score += 2
if keyword in comment.lower():     score += 1
```

**Edge cases handled:**
- Empty keyword → returns `[]` immediately (no scan)
- Missing `label` or `comment` key → treated as `""` via `meta.get(..., "")`

---

## schema.py

### `load_schema(class_name, schemas_dir)`

Lazy per-class loader. No caching — OS page cache handles repeated reads efficiently.

```mermaid
flowchart TD
    CALL["load_schema('fvBD', schemas_dir)"]
    CALL --> EXISTS{schemas_dir/fvBD.json exists?}
    EXISTS -->|"no — try subdirectory"| GLOB["schemas_dir/**/fvBD.json"]
    GLOB -->|"not found"| EMPTY["return {}"]
    EXISTS -->|"yes"| READ["read + json.loads()"]
    GLOB -->|"found"| READ
    READ --> EXTRACT["extract query-planning fields only"]
    EXTRACT --> NORM["normalise containedBy dict → list\nnormalise relationTo/From"]
    NORM --> PROPS["properties = sorted(keys)"]
    PROPS --> RETURN["return dict"]
```

### Extracted fields

Only these fields are kept — heavy fields are discarded at read time to keep tool responses token-efficient:

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

---

## filter.py

### `build_filter(class_name, filters)`

Builds an APIC `query-target-filter` string from a plain dict.

```mermaid
flowchart LR
    DICT["{\"name\": \"servers\",\n\"arpFlood\": \"yes\"}"]
    DICT --> VAL["validate class_name and each key\nagainst _IDENT_RE = [A-Za-z][A-Za-z0-9]*"]
    VAL --> PRED["build eq() predicates:\neq(fvBD.name,\"servers\")\neq(fvBD.arpFlood,\"yes\")"]
    PRED --> WRAP{"n predicates?"}
    WRAP -->|"1"| SINGLE["eq(fvBD.name,\"servers\")"]
    WRAP -->|"2+"| AND["and(eq(...),eq(...))"]
```

### Value escaping

`"` and `\` inside filter values are escaped before embedding in the APIC string:

```python
value.replace("\\", "\\\\").replace('"', '\\"')
```

This prevents filter injection when attribute values contain special characters.

### Identifier validation

Class names and attribute keys are validated against `^[A-Za-z][A-Za-z0-9]*$` before use. Values that do not match raise `FilterError`, preventing malformed APIC query strings.

| Input | Result |
|---|---|
| `"fvBD"` | valid |
| `"fv BD"` | `FilterError` (space) |
| `"123abc"` | `FilterError` (starts with digit) |
| `"fvBD; DROP"` | `FilterError` (semicolon) |
