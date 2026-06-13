# Exception Hierarchy

All exceptions are defined in [`mcp/exceptions.py`](../../mcp/exceptions.py) and inherit from a single root `AciMcpError`. This means callers can catch the whole family with one clause.

---

## Class diagram

```mermaid
classDiagram
    class AciMcpError {
        <<base>>
        inherits Exception
    }

    class ConfigurationError {
        Raised at startup
        Missing or invalid env var
    }

    class AuthenticationError {
        Incoming request has no valid API key
        (typed — middleware returns 401 directly)
    }

    class RegistryError {
        <<base>>
        Registry load failure
    }

    class DescriptionsLoadError {
        class-descriptions.json missing or invalid JSON
        Raised by load_descriptions()
    }

    class SchemaLoadError {
        +str class_name
        +str path
        jsonmeta file malformed
        Raised by load_schema()
    }

    class UnknownClassError {
        +str class_name
        +list~str~ suggestions
        +int registry_size
        Class not in descriptions index
        Raised by query()
    }

    class FilterError {
        Invalid identifier or unsafe value
        Raised by build_filter()
    }

    class ApicError {
        <<base>>
        APIC communication error
    }

    class ApicAuthError {
        +str host
        +int status
        APIC rejected login
        Raised by authenticate()
    }

    class ApicConnectionError {
        +str host
        Network error or timeout
        Wraps httpx exceptions
    }

    class ApicResponseError {
        +str url
        Response not JSON or missing imdata
        Raised by query_class()
    }

    AciMcpError <|-- ConfigurationError
    AciMcpError <|-- AuthenticationError
    AciMcpError <|-- RegistryError
    AciMcpError <|-- UnknownClassError
    AciMcpError <|-- FilterError
    AciMcpError <|-- ApicError

    RegistryError <|-- DescriptionsLoadError
    RegistryError <|-- SchemaLoadError

    ApicError <|-- ApicAuthError
    ApicError <|-- ApicConnectionError
    ApicError <|-- ApicResponseError
```

---

## Where each exception is raised

| Exception | Raised by | Trigger |
|---|---|---|
| `ConfigurationError` | `main.py` lifespan | `APIC_HOST` or `APIC_PASSWORD` missing; `MCP_PORT` not an integer |
| `AuthenticationError` | Not raised programmatically | Provided for test code — middleware returns HTTP 401 directly |
| `DescriptionsLoadError` | `registry/descriptions.py` | `class-descriptions.json` not found, unreadable, or not valid JSON |
| `SchemaLoadError` | `registry/schema.py` | jsonmeta file exists but contains malformed JSON or is empty |
| `UnknownClassError` | `main.py` `query()` tool | `class_name` not in the 15k-class descriptions index |
| `FilterError` | `registry/filter.py` | Class name or attribute key contains characters outside `[A-Za-z][A-Za-z0-9]*` |
| `ApicAuthError` | `apic/client.py` | APIC returns non-2xx on login, or still 401 after re-auth |
| `ApicConnectionError` | `apic/client.py` | `httpx.TimeoutException` or `httpx.ConnectError` on any request |
| `ApicResponseError` | `apic/client.py` | Response body is not valid JSON, or `imdata` key is absent |

---

## Catching patterns

```python
from exceptions import AciMcpError, ApicError, UnknownClassError

# Catch everything from this library
try:
    ...
except AciMcpError as exc:
    logger.error("aci-mcp error: %s", exc)

# Catch only APIC communication errors
try:
    await client.authenticate()
except ApicError as exc:
    logger.error("APIC unreachable: %s", exc)

# Catch unknown class with self-correction data
try:
    results = await query("fvBd", ctx)  # typo
except UnknownClassError as exc:
    print(exc.class_name)    # "fvBd"
    print(exc.suggestions)   # ["fvBD", "fvCEp", ...]
    print(exc.registry_size) # 15432
```
