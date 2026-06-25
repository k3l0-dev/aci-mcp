# Data Flow

## LLM mandatory tool sequence

The three tools **must** be called in this order. Skipping `search_classes` or `get_schema` causes silent empty results because the APIC returns `[]` for unknown class names or wrong attribute names without any error.

```mermaid
flowchart TD
    START([LLM receives user query]) --> S1

    S1["① search_classes(keyword)\ne.g. 'bridge domain'"]
    S1 --> D1{match found?}
    D1 -->|no| RETRY["refine keyword and retry"]
    RETRY --> S1
    D1 -->|yes| S2

    S2["② get_schema(class_name)\ne.g. 'fvBD'"]
    S2 --> NOTE2["learns: identifiedBy, containedBy,\nproperties, relationTo"]
    NOTE2 --> S3

    S3["③ query(class_name, filters, scope_dn)\ne.g. query('fvBD', filters={'name':'servers'},\nscope_dn='uni/tn-OT')"]
    S3 --> D3{result empty?}
    D3 -->|yes + bad filter| GOBACK["go back to ② — check valid properties"]
    GOBACK --> S2
    D3 -->|yes + bad scope| S3B["retry without scope_dn"]
    S3B --> DONE
    D3 -->|no| DONE

    DONE([Return objects to user])
```

---

## search_classes — internal flow

```mermaid
sequenceDiagram
    participant LLM
    participant tool as search_classes()
    participant desc as descriptions dict (in-memory)

    LLM->>tool: search_classes("bridge domain")
    tool->>desc: iterate 15k entries
    Note over desc: score per entry:<br/>class name match → +3<br/>label match → +2<br/>comment match → +1<br/>prop_labels fallback → +1 (no accumulation)<br/>Rs/Rt class name → −3 penalty
    desc-->>tool: scored list, sorted desc, capped at limit
    tool-->>LLM: [{class_name, label, comment}, ...]
```

---

## get_schema — internal flow

```mermaid
sequenceDiagram
    participant LLM
    participant tool as get_schema()
    participant fs as data/schemas/{version}/

    LLM->>tool: get_schema("fvBD")
    tool->>fs: look up fvBD.json
    Note over fs: 1. try schemas_dir/fvBD.json<br/>2. fallback: glob schemas_dir/*/fvBD.json
    fs-->>tool: raw jsonmeta object

    Note over tool: extract query-planning fields only:<br/>identifiedBy, rnFormat, containedBy (normalised to list),<br/>dnFormats, relationTo, relationFrom,<br/>properties (names only), isAbstract,<br/>isConfigurable, className, classPkg, label

    Note over tool: discard heavy fields:<br/>writeAccess, events, stats, faults,<br/>full property metadata

    tool-->>LLM: flattened schema dict (or {} if not found)
```

---

## query — internal flow

```mermaid
sequenceDiagram
    participant LLM
    participant tool as query()
    participant desc as descriptions dict
    participant filter as filter.build_filter()
    participant apic as ApicClient
    participant cisco as Cisco APIC

    LLM->>tool: query("fvBD", filters={"name":"srv"}, scope_dn="uni/tn-OT")

    tool->>desc: "fvBD" in descriptions?
    alt unknown class
        desc-->>tool: not found
        tool-->>LLM: UnknownClassError + nearest suggestions
    end

    tool->>filter: build_filter("fvBD", {"name":"srv"})
    filter-->>tool: 'eq(fvBD.name,"srv")'

    tool->>apic: query_class(...)

    alt scope_dn provided
        apic->>cisco: GET /api/mo/uni/tn-OT.json?query-target=subtree&target-subtree-class=fvBD&...
    else no scope_dn
        apic->>cisco: GET /api/class/fvBD.json?...
    end

    cisco-->>apic: {"imdata": [{fvBD: {attributes: {...}}}]}

    alt 401 or 403 (token expired)
        apic->>cisco: POST /api/aaaLogin.json (re-authenticate)
        cisco-->>apic: new APIC-cookie token
        apic->>cisco: retry original GET
        alt still 401/403
            apic-->>tool: raise ApicAuthError
        end
    end

    apic-->>tool: [{"dn": ..., "name": ..., "_class": "fvBD"}]
    tool-->>LLM: list of attribute dicts
```

---

## APIC query URL construction

The URL and query parameters built by `ApicClient.query_class()`:

```mermaid
flowchart TD
    SD{scope_dn set?}

    SD -->|"yes"| URL_MO["/api/mo/{scope_dn}.json\n?query-target=subtree\n&target-subtree-class={class_name}"]
    SD -->|"no"| URL_CLASS["/api/class/{class_name}.json"]

    URL_MO --> PARAMS
    URL_CLASS --> PARAMS

    subgraph PARAMS["Query parameters added when present"]
        P1["page-size = limit"]
        P2["query-target-filter = build_filter() + filter_expr combined with and()"]
        P3["order-by = order_by"]
        P4["rsp-subtree=children + rsp-subtree-class=X,Y (when include_children set)"]
        P5["rsp-subtree-include (faults / health / audit-logs / ...)"]
        P6["time-range (24h / 1week / date|date)"]
        P7["page = N (0-based)"]
    end
```

