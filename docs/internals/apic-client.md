# Internals: APIC Client

`mcp/apic/client.py` — async HTTP client for the Cisco APIC REST API.

---

## Class overview

```mermaid
classDiagram
    class ApicClient {
        -str _host
        -str _user
        -str _password
        -str _base
        -AsyncClient _client

        +__init__(host, user, password, verify_ssl, timeout)
        +authenticate() None
        +query_class(class_name, filters, scope_dn, ...) list
        +close() None
    }
```

A single `ApicClient` instance is created at server startup in `app_lifespan()` and shared across all tool invocations via the FastMCP lifespan context. It is **never** instantiated per-request.

---

## Authentication flow

```mermaid
sequenceDiagram
    participant client as ApicClient
    participant apic as Cisco APIC

    client->>apic: POST /api/aaaLogin.json<br/>{aaaUser: {attributes: {name, pwd}}}

    alt success (2xx)
        apic-->>client: {imdata: [{aaaLogin: {attributes: {token: "..."}}}]}
        client->>client: cookies.set("APIC-cookie", token)
    else non-2xx
        apic-->>client: HTTP 4xx/5xx
        client-->>client: raise ApicAuthError(host, status)
    else timeout / connect error
        client-->>client: raise ApicConnectionError(host, reason)
    else malformed JSON
        client-->>client: raise ApicResponseError(url, reason)
    end
```

The token is stored as a cookie on the underlying `httpx.AsyncClient` instance, so all subsequent requests include it automatically.

---

## Query flow with re-auth

```mermaid
sequenceDiagram
    participant tool as query() tool
    participant client as ApicClient
    participant apic as Cisco APIC

    tool->>client: query_class(class_name, ...)
    client->>apic: GET /api/class/{class}.json?...

    alt 401 or 403 (token expired)
        apic-->>client: HTTP 401/403
        client->>apic: POST /api/aaaLogin.json (re-authenticate)
        apic-->>client: new token
        client->>apic: GET /api/class/{class}.json?... (retry)

        alt still 401/403 after re-auth
            apic-->>client: HTTP 401/403
            client-->>tool: raise ApicAuthError(still unauthorized)
        end
    end

    apic-->>client: {imdata: [...]}
    client->>client: flatten imdata → [{attrs, _class}, ...]
    client-->>tool: list of attribute dicts
```

---

## URL construction

| Condition | URL pattern |
|---|---|
| `scope_dn` provided | `/api/mo/{scope_dn}.json?query-target=subtree&target-subtree-class={class}` |
| No `scope_dn` | `/api/class/{class}.json` |

The subtree query is more efficient for large fabrics — it limits the APIC search to the subtree under the given DN rather than scanning all objects of the class.

---

## httpx configuration

```python
httpx.AsyncClient(
    verify=verify_ssl,   # False by default — APIC labs often have self-signed certs
    timeout=30.0,        # Per-request timeout in seconds
)
```

`verify_ssl=False` suppresses SSL certificate warnings for lab APICs. Set `APIC_VERIFY_SSL=true` in production.

---

## imdata parsing

APIC returns objects in this structure:

```json
{
  "imdata": [
    {
      "fvBD": {
        "attributes": { "dn": "...", "name": "...", ... },
        "children": [
          { "fvSubnet": { "attributes": { ... } } }
        ]
      }
    }
  ]
}
```

`query_class()` flattens this to a plain list:

```python
[
  {
    "dn": "uni/tn-OT/BD-servers",
    "name": "servers",
    "_class": "fvBD",
    "_children": [
      { "ip": "10.0.1.1/24", "_class": "fvSubnet" }
    ]
  }
]
```

Children are only included when `include_children` is set.

---

## Exception mapping

| httpx exception | aci-mcp exception |
|---|---|
| `httpx.TimeoutException` | `ApicConnectionError` |
| `httpx.ConnectError` | `ApicConnectionError` |
| `resp.status_code in (401, 403)` | triggers re-auth; then `ApicAuthError` if still failing |
| `resp.json()` raises `ValueError` | `ApicResponseError` |
| `"imdata"` missing from body | `ApicResponseError` |
