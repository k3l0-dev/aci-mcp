# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""
exceptions.py

All aci-mcp exceptions in a single module so callers can import from one place.

Hierarchy
---------
AciMcpError
├── ConfigurationError        — missing or invalid startup configuration
├── AuthenticationError       — incoming request carries no valid API key
├── RegistryError             — base for registry load failures
│   ├── DescriptionsLoadError — class-descriptions.json absent or malformed
│   └── SchemaLoadError       — jsonmeta schema file malformed (exists but invalid)
├── UnknownClassError         — class name not found in the descriptions registry
├── FilterError               — invalid identifier or unsafe value in build_filter
└── ApicError                 — base for APIC communication errors
    ├── ApicAuthError         — authentication failed (bad credentials or server error)
    ├── ApicConnectionError   — APIC unreachable (network error or timeout)
    ├── ApicResponseError     — APIC returned an unexpected or malformed response
    └── ApicRequestError      — APIC rejected the request (400/404/500/... — non-auth)
"""


class AciMcpError(Exception):
    """Base exception for all aci-mcp errors."""


# ── Configuration ─────────────────────────────────────────────────────────────


class ConfigurationError(AciMcpError):
    """Required environment variable is missing or has an invalid value.

    Raised at server startup before any tool is served.
    """


# ── Authentication ────────────────────────────────────────────────────────────


class AuthenticationError(AciMcpError):
    """Incoming MCP request is missing or carrying an invalid API key.

    Raised by _authenticate() in middleware/auth.py and caught there to produce
    the 401 HTTP response. Keeping auth logic in a pure function that raises
    this exception makes it independently testable without an HTTP layer.
    """


# ── Registry ──────────────────────────────────────────────────────────────────


class RegistryError(AciMcpError):
    """Base for registry (descriptions / schemas) load failures."""


class DescriptionsLoadError(RegistryError):
    """class-descriptions.json is missing or contains invalid JSON.

    The file is mandatory — the server cannot start without it.
    Regenerate it with: aci-collect run --from descriptions
    """


class SchemaLoadError(RegistryError):
    """A jsonmeta schema file exists on disk but could not be parsed.

    Indicates a corrupted or manually edited schema file.
    """

    def __init__(self, class_name: str, path: str, reason: str) -> None:
        self.class_name = class_name
        self.path = path
        super().__init__(f"Malformed schema for '{class_name}' at {path}: {reason}")


# ── Class validation ──────────────────────────────────────────────────────────


class UnknownClassError(AciMcpError):
    """ACI class name not found in the descriptions registry.

    Raised by the query() tool when the caller supplies a class name that is
    not in the in-memory descriptions index.  Includes closest matches so the
    LLM can self-correct without an additional search_classes() round-trip.
    """

    def __init__(
        self, class_name: str, suggestions: list[str], registry_size: int
    ) -> None:
        self.class_name = class_name
        self.suggestions = suggestions
        self.registry_size = registry_size
        hint = (
            f"Closest matches: {', '.join(suggestions)}"
            if suggestions
            else "No close matches found."
        )
        super().__init__(
            f"Unknown ACI class '{class_name}' — not in the {registry_size}-class registry. "
            f"{hint} Call search_classes() to find the correct name."
        )


# ── Filter ────────────────────────────────────────────────────────────────────


class FilterError(AciMcpError):
    """Invalid input to build_filter().

    Raised when a class name or attribute contains characters outside the
    expected ACI identifier format, or when a filter value contains
    characters that cannot be safely embedded in an APIC filter string.
    """


# ── APIC communication ────────────────────────────────────────────────────────


class ApicError(AciMcpError):
    """Base for all APIC communication errors."""


class ApicAuthError(ApicError):
    """APIC authentication failed.

    Raised when the APIC returns a non-2xx response to the login request,
    or when re-authentication after a 401/403 still fails.
    """

    def __init__(self, host: str, status: int, detail: str = "") -> None:
        self.host = host
        self.status = status
        msg = f"APIC authentication failed for {host} (HTTP {status})"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


class ApicConnectionError(ApicError):
    """APIC is unreachable — network error or request timeout.

    Wraps httpx.ConnectError and httpx.TimeoutException so callers do not
    need to import httpx to handle connectivity problems. ApicClient retries
    a bounded number of times (see ApicClient.__init__'s retry_attempts) with
    a short backoff before giving up — this is raised only once that budget
    is exhausted, never on the first connection failure alone.
    """

    def __init__(self, host: str, reason: str) -> None:
        self.host = host
        super().__init__(f"Cannot reach APIC at {host}: {reason}")


class ApicResponseError(ApicError):
    """APIC returned an unexpected or malformed response body.

    Raised when the response is not valid JSON, or when the expected
    'imdata' key is absent from an otherwise successful response.
    """

    def __init__(self, url: str, reason: str) -> None:
        self.url = url
        super().__init__(f"Unexpected APIC response from {url}: {reason}")


class ApicRequestError(ApicError):
    """APIC rejected a request with a non-2xx, non-authentication status.

    Raised by ApicClient.query_class(), get_by_dn(), and count_class() (all
    three share the same request path) for any status code other than
    401/403 (which trigger the re-auth-and-retry flow instead) — typically
    400 for a malformed filter_expr or query-target-filter, or a transient
    404/500/502/503/504 that never recovered within the retry budget (a
    permanent status like 400 is raised on the first attempt; a transient
    one is retried a bounded number of times first — see
    ApicClient.__init__'s retry_attempts).

    Carries the raw HTTP status and, when the response body follows APIC's
    usual error shape (`imdata[0].error.attributes.text`), the human-readable
    reason APIC supplied — so an LLM caller gets an actionable message
    instead of an opaque httpx.HTTPStatusError.
    """

    def __init__(self, url: str, status: int, apic_text: str = "") -> None:
        self.url = url
        self.status = status
        self.apic_text = apic_text
        msg = f"APIC request to {url} failed with HTTP {status}"
        if apic_text:
            msg += f": {apic_text}"
        super().__init__(msg)
