# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
apic/client.py

Async HTTP client for the Cisco APIC REST API.

Handles authentication (cookie-based token), class queries, and subtree queries.
A single ApicClient instance is created at server startup via the lifespan and
shared across all tool invocations through the FastMCP context.

query_class() returns a QueryResult (objects + the APIC-reported totalCount +
a completeness flag) rather than a bare list, so callers can tell a partial
page from the whole matching set — see QueryResult and the fetch_all param.

APIC endpoint reference:
  POST /api/aaaLogin.json                          — authenticate
  GET  /api/class/{cls}.json                       — fabric-wide class query
  GET  /api/mo/{dn}.json?query-target=subtree&...  — subtree query under a DN
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from niwashi_mcp.exceptions import (
    ApicAuthError,
    ApicConnectionError,
    ApicRequestError,
    ApicResponseError,
    FilterError,
)
from niwashi_mcp.registry.filter import build_filter

logger = logging.getLogger("niwashi-mcp.apic")

# fetch_all safety caps — a fabric-wide class scan with no scope_dn can be
# unbounded (a bad filter, or a class with tens of thousands of instances),
# so a page-loop needs its own hard stop independent of the caller's limit.
_MAX_PAGES = 25
_MAX_OBJECTS = 5000

# HTTP statuses treated as transient and worth a bounded retry, rather than an
# immediate permanent failure. 404 is included deliberately: nowhere in this
# client does a real "doesn't exist" condition surface as an HTTP 404 —
# query_class()/count_class() only ever reach the backend with a class name
# already validated against the local registry, and get_by_dn()'s "no object
# at this DN" case is APIC returning 200 with an empty imdata list, not a
# 404. An observed 404 here is therefore presumptively infrastructure noise
# (a proxy/load-balancer hiccup in front of the APIC), not an application
# error — confirmed empirically by repeating one otherwise-successful
# request several times against a live fabric and seeing it intermittently
# fail with a bare 404 (no APIC error body) between successes. A genuine
# application-level rejection (a malformed filter_expr, for instance) comes
# back as 400, which is deliberately NOT in this set — retrying a real 400
# would only add latency to a failure that will never succeed.
_TRANSIENT_STATUSES = frozenset({404, 500, 502, 503, 504})


def _extract_apic_error_text(resp: httpx.Response) -> str:
    """Best-effort extraction of the human-readable APIC error message.

    APIC embeds a reason for 4xx/5xx failures (malformed filter, unknown DN,
    internal error, ...) at `imdata[0].error.attributes.text` in the response
    body. This is opportunistic: any shape mismatch (non-JSON body, empty
    imdata, missing keys) simply yields "" rather than raising, since the
    caller (ApicRequestError) already has a usable message from the HTTP
    status alone.

    Args:
        resp: The httpx response object for the failed request.

    Returns:
        The APIC-supplied error text, or "" when unavailable.
    """
    try:
        body = resp.json()
        return body["imdata"][0]["error"]["attributes"]["text"]
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


# Characters and shapes that must never reach the path of an APIC URL. A DN is
# interpolated straight into `/api/mo/{dn}.json`, so anything that can leave
# that path segment changes *which endpoint* the server's authenticated session
# reaches — not merely which object it asks for.
#
# Measured before the guard existed: `uni/tn-OT/../../api/aaaListDomains`
# resolved to `https://apic/api/mo/api/aaaListDomains.json`. A DN arrives from
# an LLM, and an LLM's input can be attacker-influenced, so this is a
# confused-deputy hazard rather than a theoretical one.
_DN_FORBIDDEN = ("?", "#", "\\", "\n", "\r", "\t", "\x00")


def validate_dn(dn: str, *, field: str = "dn") -> str:
    """Return `dn` unchanged, or raise if it could leave its URL path segment.

    Deliberately a rejection rather than a sanitisation: silently rewriting a
    DN would answer a question the caller did not ask, which is the failure
    mode this whole server is built to avoid. A malformed DN is a mistake worth
    surfacing.

    Args:
        dn: The distinguished name to check.
        field: Parameter name to quote in the error, so the caller knows which
            argument to fix.

    Raises:
        FilterError: The DN is empty, absolute, contains a traversal segment,
            or carries a character that would escape the path.
    """
    if not dn or not dn.strip():
        raise FilterError(f"{field} must not be empty")

    if dn.startswith("/"):
        raise FilterError(
            f"{field} must be relative, got {dn!r} — an ACI DN never starts with '/'"
        )

    for char in _DN_FORBIDDEN:
        if char in dn:
            raise FilterError(
                f"{field} contains {char!r}, which would escape the URL path: {dn!r}"
            )

    # `..` is only a traversal as a whole segment; it is legitimate inside a
    # name (a tenant may be called `a..b`), so match segments, not substrings.
    if any(segment == ".." for segment in dn.split("/")):
        raise FilterError(
            f"{field} contains a '..' path segment, which would target a "
            f"different APIC endpoint: {dn!r}"
        )

    return dn


@dataclass
class QueryResult:
    """Result of a query_class() call — objects plus the true match size.

    Attributes:
        objects:          Parsed attribute dicts, in the same shape query_class()
                          has always returned (each with a "_class" key, and
                          "_children" when include_children was set).
        total_available:  The APIC-reported totalCount for the query — the
                          true number of matching objects fabric-wide (or
                          subtree-wide with scope_dn), independent of how many
                          were actually fetched into `objects`.
        complete:         True unless fetch_all=True and the page loop stopped
                          early after hitting _MAX_PAGES/_MAX_OBJECTS before
                          exhausting all matching objects.
    """

    objects: list[dict[str, Any]] = field(default_factory=list)
    total_available: int = 0
    complete: bool = True


class ApicClient:
    """Async APIC REST client with session cookie management.

    Create one instance at startup, call authenticate(), then reuse across
    requests.  Call close() during shutdown to release the underlying
    httpx.AsyncClient.
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        verify_ssl: bool = False,
        timeout: float = 30.0,
        retry_attempts: int = 3,
        retry_backoff_base: float = 0.2,
    ) -> None:
        """Initialise the client without opening a connection.

        Args:
            host:               APIC hostname or IP (no scheme), e.g. "10.41.71.11".
            user:               APIC username, typically "admin".
            password:           APIC password. Never logged.
            verify_ssl:         Set True to enforce TLS certificate verification.
            timeout:            Per-request timeout in seconds (default 30 s).
            retry_attempts:     Total attempts (including the first) for a
                                transient failure — see _TRANSIENT_STATUSES —
                                before it is raised as a permanent error.
            retry_backoff_base: Base delay in seconds for the exponential
                                backoff between retries (doubles each attempt,
                                capped at 2s). Set to 0 in tests to avoid
                                real sleeps.
        """
        self._host = host
        self._user = user
        self._password = password
        self._base = f"https://{host}"
        self._client = httpx.AsyncClient(verify=verify_ssl, timeout=timeout)
        self._retry_attempts = retry_attempts
        self._retry_backoff_base = retry_backoff_base

    def _backoff_delay(self, attempt: int) -> float:
        """Delay before retry attempt `attempt` (1-based), exponential and capped.

        attempt=1 → base, attempt=2 → 2xbase, ... capped at 2.0s so a run of
        transient failures can't turn a single tool call into a multi-second
        stall for the LLM agent waiting on it.
        """
        return min(self._retry_backoff_base * (2 ** (attempt - 1)), 2.0)

    async def authenticate(self) -> None:
        """Obtain an APIC session token and store it as a cookie.

        Sends a POST to /api/aaaLogin.json and sets the returned token as the
        APIC-cookie on the underlying httpx client so all subsequent requests
        are authenticated automatically.

        Raises:
            ApicAuthError:       APIC returned a non-2xx response to the login.
            ApicConnectionError: Host unreachable or request timed out.
            ApicResponseError:   Response body is not valid JSON or missing token.
        """
        url = f"{self._base}/api/aaaLogin.json"
        payload = {
            "aaaUser": {"attributes": {"name": self._user, "pwd": self._password}}
        }
        try:
            resp = await self._client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise ApicConnectionError(self._host, f"request timed out: {exc}") from exc
        except httpx.ConnectError as exc:
            raise ApicConnectionError(self._host, str(exc)) from exc

        if not resp.is_success:
            raise ApicAuthError(self._host, resp.status_code)

        try:
            data = resp.json()
            token: str = data["imdata"][0]["aaaLogin"]["attributes"]["token"]
        except (KeyError, IndexError, ValueError) as exc:
            raise ApicResponseError(
                url, f"unexpected login response body: {exc}"
            ) from exc

        self._client.cookies.set("APIC-cookie", token)
        logger.info("Authenticated to APIC as %s @ %s", self._user, self._host)

    def _build_query_params(
        self,
        class_name: str,
        filters: dict[str, str],
        scope_dn: str,
        limit: int,
        order_by: str,
        include_children: list[str] | None,
        filter_expr: str | None,
        rsp_subtree_include: str | None,
        time_range: str | None,
        config_only: bool,
    ) -> tuple[str, dict[str, str]]:
        """Build the (url, params) pair shared by every query_class() page request.

        `page` is deliberately excluded — the caller sets it per request, since
        a fetch_all loop needs the same base params with only `page` varying.
        """
        params: dict[str, str] = {"page-size": str(limit)}

        if scope_dn:
            url = f"{self._base}/api/mo/{validate_dn(scope_dn, field='scope_dn')}.json"
            params["query-target"] = "subtree"
            params["target-subtree-class"] = class_name
        else:
            url = f"{self._base}/api/class/{class_name}.json"

        eq_filter = build_filter(class_name, filters)
        if filter_expr and eq_filter:
            params["query-target-filter"] = f"and({filter_expr},{eq_filter})"
        elif filter_expr:
            params["query-target-filter"] = filter_expr
        elif eq_filter:
            params["query-target-filter"] = eq_filter

        if order_by:
            params["order-by"] = order_by
        if include_children:
            params["rsp-subtree"] = "children"
            params["rsp-subtree-class"] = ",".join(include_children)
        if rsp_subtree_include:
            params["rsp-subtree-include"] = rsp_subtree_include
            # rsp-subtree-include selects *which* categories come back; it does
            # not ask for a subtree at all. Sent alone the APIC returns the
            # bare objects, so "give me BDs with their faults" silently became
            # "give me the BDs that have faults" — and the agent concluded
            # there were none.
            params.setdefault("rsp-subtree", "children")
        if time_range:
            params["time-range"] = time_range
        if config_only:
            params["rsp-prop-include"] = "config-only"

        return url, params

    @staticmethod
    def _parse_query_objects(
        body: dict[str, Any], include_children: list[str] | None
    ) -> list[dict[str, Any]]:
        """Parse one APIC class/subtree response body into attribute dicts."""
        objects: list[dict[str, Any]] = []
        for item in body.get("imdata", []):
            for cls, obj in item.items():
                attrs: dict[str, Any] = dict(obj.get("attributes", {}))
                attrs["_class"] = cls
                # Extract whatever children the APIC actually returned, not
                # only those this call asked for by class. rsp-subtree-include
                # produces children too (faults, health, audit-logs) and gating
                # on include_children dropped them on the floor, with the
                # object itself arriving intact — a silent partial answer.
                if "children" in obj:
                    children: list[dict[str, Any]] = []
                    for child_item in obj["children"]:
                        for child_cls, child_obj in child_item.items():
                            child_attrs = dict(child_obj.get("attributes", {}))
                            child_attrs["_class"] = child_cls
                            children.append(child_attrs)
                    attrs["_children"] = children
                objects.append(attrs)
        return objects

    @staticmethod
    def _parse_total_available(body: dict[str, Any], fallback: int) -> int:
        """Coerce the APIC-reported totalCount, defensively.

        A missing or non-numeric totalCount falls back to `fallback` rather
        than raising.  Shared by query_class() (where `fallback` is the number
        of objects parsed from the page) and count_class() (where it is the
        number of objects in imdata), so both report the size of a result set
        from the same field and can never disagree about it.
        """
        try:
            return int(body.get("totalCount", fallback))
        except (TypeError, ValueError):
            return fallback

    async def query_class(
        self,
        class_name: str,
        filters: dict[str, str],
        scope_dn: str = "",
        limit: int = 20,
        order_by: str = "",
        include_children: list[str] | None = None,
        filter_expr: str | None = None,
        rsp_subtree_include: str | None = None,
        time_range: str | None = None,
        page: int | None = None,
        config_only: bool = False,
        fetch_all: bool = False,
    ) -> QueryResult:
        """Query ACI objects by class, optionally scoped to a subtree DN.

        When `scope_dn` is provided the request targets the APIC subtree
        endpoint (`/api/mo/{scope_dn}.json?query-target=subtree`) which is
        more efficient than a fabric-wide class scan for large deployments.

        When `include_children` is provided, the APIC `rsp-subtree=children`
        parameter is added so each returned object embeds its direct children
        of the listed classes as `_children` — equivalent to
        `moquery -x rsp-subtree=children -x rsp-subtree-class=X,Y`.

        The APIC filter string is built internally from `filters` via
        registry.filter.build_filter() — callers pass plain dicts.

        Args:
            class_name:          ACI class to query, e.g. "fvBD".
            filters:             Attribute equality filters {attr: value}.
            scope_dn:            Optional parent DN to scope the query.
            limit:               Maximum objects to return per page (APIC
                                 page-size). Also the page size used internally
                                 when fetch_all=True.
            order_by:            Optional ordering expression.
            include_children:    Child class names to embed via rsp-subtree=children.
            filter_expr:         Raw APIC filter string for complex predicates,
                                 e.g. 'wcard(fvBD.dn,"uni/tn-OT")' or
                                 'and(ne(fabricNode.role,"controller"),...)'.
                                 Combined with `filters` via and() when both set.
            rsp_subtree_include: Subtree categories to include, e.g. "faults",
                                 "health", "audit-logs", "faults,no-scoped".
            time_range:          Time range for log record classes, e.g. "24h",
                                 "1week", "2024-01-01|2024-01-31".
            page:                Page number for explicit manual pagination
                                 (0-based). Ignored when fetch_all=True, which
                                 always walks pages from 0.
            config_only:         When True, add rsp-prop-include=config-only so the
                                 APIC returns only user-configurable attributes and
                                 drops operational/internal noise (~40 attrs → the
                                 handful that define the object's intended config).
            fetch_all:           When True, loop pages (0, 1, 2, ...) using `limit`
                                 as the page size, accumulating every matching
                                 object into one QueryResult instead of returning
                                 just the first page. Stops at the first short
                                 page (fewer objects than `limit` — the natural
                                 end) or at the _MAX_PAGES/_MAX_OBJECTS safety
                                 cap, whichever comes first; the latter sets
                                 `complete=False` on the returned QueryResult.

        Returns:
            A QueryResult carrying:
              objects:         Attribute dicts with "_class" key (and
                               "_children" when include_children is set).
              total_available: The APIC-reported totalCount — the true number
                               of matching objects, regardless of how many
                               were actually fetched.
              complete:        False only when fetch_all=True hit the safety
                               cap before exhausting all matching objects.

        Raises:
            ApicAuthError:     Both the initial request and the re-auth retry
                               were rejected with 401/403.
            ApicConnectionError: The APIC host is still unreachable, or the
                               request still timed out, after exhausting the
                               retry budget (see _TRANSIENT_STATUSES).
            ApicRequestError:  APIC returned a non-2xx, non-auth status that
                               persisted across the retry budget — e.g. 400
                               for a malformed filter_expr (never retried,
                               since it is a permanent error), or a transient
                               500/502/503/504/404 that never recovered.
                               Carries the HTTP status and, when present, the
                               APIC-supplied error text.
            ApicResponseError: The response body is not valid JSON, or is
                               missing the expected 'imdata' key.
        """
        url, base_params = self._build_query_params(
            class_name, filters, scope_dn, limit, order_by, include_children,
            filter_expr, rsp_subtree_include, time_range, config_only,
        )

        if not fetch_all:
            params = dict(base_params)
            if page is not None:
                params["page"] = str(page)
            logger.debug("GET %s params=%s", url, params)
            body = await self._request_json(url, params)
            objects = self._parse_query_objects(body, include_children)
            total_available = self._parse_total_available(body, len(objects))
            logger.debug("query_class(%s) → %d objects", class_name, len(objects))
            return QueryResult(objects=objects, total_available=total_available)

        objects: list[dict[str, Any]] = []
        total_available = 0
        complete = True
        for page_num in range(_MAX_PAGES):
            params = dict(base_params)
            params["page"] = str(page_num)
            logger.debug("GET %s params=%s", url, params)
            body = await self._request_json(url, params)
            page_objects = self._parse_query_objects(body, include_children)
            objects.extend(page_objects)
            total_available = self._parse_total_available(body, len(objects))

            if len(page_objects) < limit:
                break
            if len(objects) >= _MAX_OBJECTS:
                complete = False
                break
        else:
            complete = False

        logger.debug(
            "query_class(%s, fetch_all=True) → %d/%d objects, complete=%s",
            class_name, len(objects), total_available, complete,
        )
        return QueryResult(
            objects=objects, total_available=total_available, complete=complete
        )

    async def _send(self, url: str, params: dict[str, str]) -> httpx.Response:
        """Issue one authenticated GET, handling 401/403 re-auth-and-retry.

        This is the low-level transport step shared by every endpoint via
        _request_json(): it does not interpret the response status beyond
        401/403 (re-authenticate once and retry) — deciding whether any
        other status is a transient condition worth retrying or a permanent
        failure is _request_json()'s job, not this method's, so that the
        retry budget in _request_json() governs the *outer* attempt loop
        while this method's own one-shot re-auth stays a single, independent
        step within each of those attempts.

        Raises:
            ApicConnectionError: Host unreachable or request timed out.
            ApicAuthError:       Still unauthorized (401/403) after re-authenticating.
        """
        try:
            resp = await self._client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise ApicConnectionError(self._host, f"request timed out: {exc}") from exc
        except httpx.ConnectError as exc:
            raise ApicConnectionError(self._host, str(exc)) from exc

        if resp.status_code in (401, 403):
            logger.warning(
                "APIC returned %d — re-authenticating and retrying", resp.status_code
            )
            await self.authenticate()
            try:
                resp = await self._client.get(url, params=params)
            except httpx.TimeoutException as exc:
                raise ApicConnectionError(
                    self._host, f"request timed out after re-auth: {exc}"
                ) from exc
            except httpx.ConnectError as exc:
                raise ApicConnectionError(self._host, str(exc)) from exc
            if resp.status_code in (401, 403):
                raise ApicAuthError(
                    self._host,
                    resp.status_code,
                    "still unauthorized after re-authentication",
                )

        return resp

    async def _request_json(
        self, url: str, params: dict[str, str]
    ) -> dict[str, Any]:
        """Issue an authenticated GET, retrying transient failures, and return
        the parsed APIC JSON body.

        Shared by query_class(), get_by_dn(), and count_class() — the single
        transport path for every read this client makes.

        Retries up to `self._retry_attempts` total attempts (small exponential
        backoff between them — see _backoff_delay()) when a request fails
        with either a connection-level error (timeout/refused) or a status in
        _TRANSIENT_STATUSES (404/500/502/503/504 — see that constant's
        docstring for why 404 is included here specifically). A genuine
        application-level error — a 400 from a malformed filter_expr, for
        instance — is raised immediately on the first attempt, since retrying
        it would only add latency to a failure that will never succeed. The
        401/403 re-authenticate-and-retry flow (see _send()) is a separate,
        one-shot step nested inside each attempt and is unaffected by this
        retry budget either way.

        Args:
            url:    Absolute APIC URL to GET.
            params: Query-string parameters.

        Returns:
            The decoded response body — a dict guaranteed to carry an "imdata" key.

        Raises:
            ApicConnectionError: Host unreachable or request timed out, after
                                 exhausting the retry budget.
            ApicAuthError:       Still unauthorized after re-authentication —
                                 never retried beyond _send()'s own one-shot
                                 re-auth, since a bad credential won't fix
                                 itself on a second attempt.
            ApicRequestError:    APIC returned a non-2xx, non-auth status that
                                 was either permanent (e.g. 400) or transient
                                 but never recovered within the retry budget.
                                 Carries the HTTP status and, when present,
                                 the APIC-supplied error text.
            ApicResponseError:   Body is not valid JSON or lacks 'imdata'.
        """
        logger.debug("GET %s params=%s", url, params)
        last_exc: ApicConnectionError | None = None

        for attempt in range(1, self._retry_attempts + 1):
            try:
                resp = await self._send(url, params)
            except ApicConnectionError as exc:
                last_exc = exc
                if attempt < self._retry_attempts:
                    logger.warning(
                        "Connection error (attempt %d/%d) — retrying: %s",
                        attempt, self._retry_attempts, exc,
                    )
                    await asyncio.sleep(self._backoff_delay(attempt))
                    continue
                raise

            if resp.status_code in _TRANSIENT_STATUSES:
                if attempt < self._retry_attempts:
                    logger.warning(
                        "APIC returned transient status %d (attempt %d/%d) — retrying",
                        resp.status_code, attempt, self._retry_attempts,
                    )
                    await asyncio.sleep(self._backoff_delay(attempt))
                    continue
                raise ApicRequestError(
                    url, resp.status_code, _extract_apic_error_text(resp)
                )

            if resp.status_code >= 400:
                raise ApicRequestError(
                    url, resp.status_code, _extract_apic_error_text(resp)
                )

            try:
                body = resp.json()
            except ValueError as exc:
                raise ApicResponseError(url, f"response is not valid JSON: {exc}") from exc

            if "imdata" not in body:
                raise ApicResponseError(url, "response body missing 'imdata' key")

            return body

        # Unreachable when self._retry_attempts >= 1 (every loop iteration
        # either returns or raises) — satisfies the type checker without
        # papering over a real bug if it somehow is reached.
        raise last_exc or ApicConnectionError(self._host, "retry budget exhausted")

    async def get_by_dn(
        self,
        dn: str,
        config_only: bool = False,
        include_children: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Fetch a single managed object directly by its Distinguished Name.

        Targets the APIC managed-object endpoint (`GET /api/mo/{dn}.json`), the
        fast path when the exact DN is already known — no class scan, no filter.

        Args:
            dn:               Full Distinguished Name of the object, e.g.
                              "uni/tn-OT/BD-servers".
            config_only:      When True, add rsp-prop-include=config-only so only
                              user-configurable attributes are returned.
            include_children: Child class names to embed via rsp-subtree=children;
                              each appears in the returned object's "_children".

        Returns:
            The object's attribute dict with a "_class" key (and "_children" when
            include_children is set), or None when no object exists at that DN —
            the APIC returns an empty imdata list for a missing DN, which the
            caller turns into an explicit not-found message.

        Raises:
            ApicConnectionError / ApicAuthError / ApicRequestError / ApicResponseError on
            or protocol failures (see _request_json).
        """
        url = f"{self._base}/api/mo/{validate_dn(dn)}.json"
        params: dict[str, str] = {}
        if config_only:
            params["rsp-prop-include"] = "config-only"
        if include_children:
            params["rsp-subtree"] = "children"
            params["rsp-subtree-class"] = ",".join(include_children)

        body = await self._request_json(url, params)
        imdata = body.get("imdata", [])
        if not imdata:
            logger.debug("get_by_dn(%s) → not found", dn)
            return None

        for cls, obj in imdata[0].items():
            attrs: dict[str, Any] = dict(obj.get("attributes", {}))
            attrs["_class"] = cls
            # Same rule as query_class: take whatever children came back.
            if "children" in obj:
                children: list[dict[str, Any]] = []
                for child_item in obj["children"]:
                    for child_cls, child_obj in child_item.items():
                        child_attrs = dict(child_obj.get("attributes", {}))
                        child_attrs["_class"] = child_cls
                        children.append(child_attrs)
                attrs["_children"] = children
            return attrs
        return None

    async def count_class(
        self,
        class_name: str,
        filters: dict[str, str],
        scope_dn: str = "",
        filter_expr: str | None = None,
    ) -> int:
        """Count ACI objects of a class without transferring their attributes.

        Issues the same class or subtree query as query_class() with a page size
        of 1, then reads the APIC-reported `totalCount` from the response
        envelope — the true size of the matching set, independent of how many
        objects were actually fetched.  Exactly one object comes back instead of
        none; every other match stays on the APIC, so this is still far cheaper
        than fetching a whole result set just to measure it.  Filtering and
        scoping behave exactly as in query_class().

        This deliberately does NOT use the APIC `rsp-subtree-include=count`
        mechanism, even though that is the obvious idiom for a count and is what
        this method used until the tally it produces was measured against
        reality.  On APIC 6.0(9c) the returned `moCount` disagrees with the
        actual size of the result set, without ever erroring, so a caller
        cannot tell a wrong tally from a right one:

          count("fvBD")                        moCount 203   actual 403
          count("fvTenant")                    moCount  36   actual  48
          count("fvBD", filters={arpFlood:no}) moCount  99   actual 203
          count("faultInst")                   moCount 420   actual 420  (right)
          count("fvBD", scope_dn=<tenant A>)   moCount   0   actual 192
          count("fvBD", scope_dn=<tenant B>)   moCount 128   actual 128  (right)

        The failure is data-dependent, not systematic: sweeping every tenant on
        the lab fabric, 5 of the 28 holding bridge domains reported a scoped
        count of 0 while the subtree really held 1 to 192 of them; the other 23
        were exact.  It is however perfectly deterministic — six consecutive
        calls returned the same wrong 0 for the same tenant, and the same right
        answer for its neighbour — so it cannot be papered over with a retry.

        A wrong-but-plausible tally is bad; the 0 is worse.  Zero is the one
        answer a caller will not question, because it reads as a legitimate
        finding ("this tenant has no bridge domains") rather than as a failed
        lookup, which is exactly the error-as-answer failure mode the tool
        layer works to prevent everywhere else.

        `totalCount` was exact in every case measured, and is already the number
        query_class() reports as `total_available`, so count() and query() can
        no longer disagree about the size of the same result set.

        Measured on an APIC 6.0(9c) simulator; the `moCount` behaviour has not
        been re-confirmed against hardware.  That does not weaken the choice
        here — `totalCount` is exact on both, and is the mechanism this client
        already relies on everywhere else.  The root cause of the `moCount`
        discrepancy was not determined and is not needed: the field is simply
        not used any more.

        Args:
            class_name:  ACI class to count, e.g. "fvBD".
            filters:     Attribute equality filters {attr: value}.
            scope_dn:    Optional parent DN to scope the count to a subtree.
            filter_expr: Raw APIC filter string, combined with `filters` via and().

        Returns:
            The number of matching objects as an int.

        Raises:
            ApicConnectionError / ApicAuthError / ApicRequestError / ApicResponseError on
            or protocol failures (see _request_json).
        """
        params: dict[str, str] = {"page-size": "1"}
        if scope_dn:
            url = f"{self._base}/api/mo/{validate_dn(scope_dn, field='scope_dn')}.json"
            params["query-target"] = "subtree"
            params["target-subtree-class"] = class_name
        else:
            url = f"{self._base}/api/class/{class_name}.json"

        eq_filter = build_filter(class_name, filters)
        if filter_expr and eq_filter:
            params["query-target-filter"] = f"and({filter_expr},{eq_filter})"
        elif filter_expr:
            params["query-target-filter"] = filter_expr
        elif eq_filter:
            params["query-target-filter"] = eq_filter

        body = await self._request_json(url, params)
        count = self._parse_total_available(body, len(body.get("imdata", [])))
        logger.debug("count_class(%s) → %d", class_name, count)
        return count

    async def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.aclose()
