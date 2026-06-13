# Copyright (c) 2026 Khalid El-Ouiali — Monark AIOPS SRL. All rights reserved.
"""
exceptions.py

All aci-mcp exceptions in a single module so callers can import from one place.

Hierarchy
---------
AciMcpError
├── ConfigurationError        — missing or invalid startup configuration
├── AuthenticationError       — incoming request lacks a valid API key
├── RegistryError             — base for registry load failures
│   ├── DescriptionsLoadError — class-descriptions.json absent or malformed
│   └── SchemaLoadError       — jsonmeta schema file malformed (exists but invalid)
├── UnknownClassError         — class name not found in the descriptions registry
├── FilterError               — invalid identifier or unsafe value in build_filter
└── ApicError                 — base for APIC communication errors
    ├── ApicAuthError         — authentication failed (bad credentials or server error)
    ├── ApicConnectionError   — APIC unreachable (network error or timeout)
    └── ApicResponseError     — APIC returned an unexpected or malformed response
"""

from __future__ import annotations


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

    Not raised programmatically by the middleware (which returns a 401 HTTP
    response directly) — provided here so test code and future callers can
    catch a typed exception instead of inspecting response status codes.
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
    need to import httpx to handle connectivity problems.
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
