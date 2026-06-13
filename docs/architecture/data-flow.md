# Data Flow

## LLM mandatory tool sequence

The three tools **must** be called in this order. Skipping `search_classes` or `get_schema` causes silent empty results because the APIC returns `[]` for unknown class names or wrong attribute names without any error.

```mermaid
flowchart TD
    START([LLM receives user query]) --> S1

    S1["① search_classes(keyword)<br/>e.g. 'bridge domain'"]
    S1 --> D1{match found?}
    D1 -->|no| RETRY["refine keyword\nand retry"]
    RETRY --> S1
    D1 -->|yes| S2

    S2["② get_schema(class_name)<br/>e.g. 'fvBD'"]
    S2 --> NOTE2["learns: identifiedBy, containedBy,\nproperties, relationTo"]
    NOTE2 --> S3

    S3["③ query(class_name, filters, scope_dn)<br/>e.g. query('fvBD', filters={'name':'servers'},\nscope_dn='uni/tn-OT')"]
    S3 --> D3{result empty?}
    D3 -->|yes + bad filter| GOBACK["go back to ② —\ncheck valid properties"]
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
    participant desc as descriptions dict<br/>(in-memory)

    LLM->>tool: search_classes("bridge domain")
    tool->>desc: iterate 15k entries
    Note over desc: score per entry:<br/>class name match → +3<br/>label match → +2<br/>comment match → +1
    desc-->>tool: scored list, sorted desc, limit 10
    tool-->>LLM: [{class_name, label, comment}, ...]
```

---

## get_schema — internal flow

```mermaid
sequenceDiagram
    participant LLM
    participant tool as get_schema()
    participant fs as data/schemas/<br/>jsonmeta files

    LLM->>tool: get_schema("fvBD")
    tool->>fs: read fvBD.json (lazy — first call only)
    fs-->>tool: raw jsonmeta object

    Note over tool: extract only query-planning fields:<br/>identifiedBy, rnFormat, containedBy,<br/>dnFormats, relationTo, relationFrom,<br/>properties (names only), isAbstract,<br/>isConfigurable, className, classPkg, label

    Note over tool: discard heavy fields:<br/>writeAccess, events, stats, faults

    tool-->>LLM: flattened schema dict
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
        tool-->>LLM: UnknownClassError + suggestions
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

    alt 401 / 403
        apic->>cisco: POST /api/aaaLogin.json (re-auth)
        cisco-->>apic: new token
        apic->>cisco: retry original GET
    end

    apic-->>tool: [{"dn": ..., "name": ..., "_class": "fvBD"}]
    tool-->>LLM: list of attribute dicts
```

---

## APIC query URL construction

The URL and query parameters built by `ApicClient.query_class()`:

```mermaid
flowchart LR
    subgraph url["URL"]
        U1["scope_dn set?"]
        U1 -->|yes| URL_MO["/api/mo/{scope_dn}.json<br/>+ query-target=subtree<br/>+ target-subtree-class=CLASS"]
        U1 -->|no| URL_CLASS["/api/class/CLASS.json"]
    end

    subgraph params["Query parameters"]
        P1["page-size = limit"]
        P2["query-target-filter = build_filter() result"]
        P3["order-by = order_by"]
        P4["rsp-subtree=children<br/>+ rsp-subtree-class=X,Y"]
        P5["rsp-subtree-include = faults / health / ..."]
        P6["time-range = 24h / 1week / date|date"]
        P7["page = N"]
    end
```

---

## Schema-collector pipeline

The `aci-collect` CLI runs four sequential steps to build the data files consumed by the MCP server:

```mermaid
flowchart LR
    ENV[".env\nAPIC credentials"]

    subgraph pipeline["aci-collect run"]
        direction TB
        S1["cobra<br/>Download acimodel wheel\nfrom APIC /cobra/_downloads"]
        S2["classes<br/>Extract Mo subclasses\nfrom wheel → classes.yaml"]
        S3["schemas<br/>Fetch jsonmeta JSON\nfor each class\n→ mo-schemas/*.json"]
        S4["descriptions<br/>Build label+comment index\n→ data/class-descriptions.json"]
        S1 --> S2 --> S3 --> S4
    end

    subgraph out["Outputs"]
        O1["cobra-sdk/<br/>(gitignored)"]
        O2["classes.yaml<br/>(gitignored)"]
        O3["mo-schemas/*.json<br/>(gitignored — 15k+ files)"]
        O4["data/class-descriptions.json<br/>(committed)"]
    end

    ENV --> pipeline
    S1 --> O1
    S2 --> O2
    S3 --> O3
    S4 --> O4

    O4 -->|"read at MCP startup"| MCP["aci-mcp server"]
    O3 -->|"read on demand by get_schema()"| MCP
```
