# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
apic/client.py

Async HTTP client for the Cisco APIC REST API.

Handles authentication (cookie-based token), class queries, and subtree queries.
A single ApicClient instance is created at server startup via the lifespan and
shared across all tool invocations through the FastMCP context.

APIC endpoint reference:
  POST /api/aaaLogin.json                          — authenticate
  GET  /api/class/{cls}.json                       — fabric-wide class query
  GET  /api/mo/{dn}.json?query-target=subtree&...  — subtree query under a DN
"""

import logging
from typing import Any

import httpx
from exceptions import ApicAuthError, ApicConnectionError, ApicResponseError
from registry.filter import build_filter

logger = logging.getLogger("aci-mcp.apic")


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
    ) -> None:
        """Initialise the client without opening a connection.

        Args:
            host:       APIC hostname or IP (no scheme), e.g. "10.41.71.11".
            user:       APIC username, typically "admin".
            password:   APIC password. Never logged.
            verify_ssl: Set True to enforce TLS certificate verification.
            timeout:    Per-request timeout in seconds (default 30 s).
        """
        self._host = host
        self._user = user
        self._password = password
        self._base = f"https://{host}"
        self._client = httpx.AsyncClient(verify=verify_ssl, timeout=timeout)

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
    ) -> list[dict[str, Any]]:
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
            limit:               Maximum objects to return (APIC page-size).
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
            page:                Page number for paginated results (0-based).
            config_only:         When True, add rsp-prop-include=config-only so the
                                 APIC returns only user-configurable attributes and
                                 drops operational/internal noise (~40 attrs → the
                                 handful that define the object's intended config).

        Returns:
            List of attribute dicts with "_class" key.  When include_children
            is set, each dict also contains "_children": list of child dicts,
            each with their own "_class" key.

        Raises:
            httpx.HTTPStatusError: On non-2xx APIC responses.
            httpx.RequestError:    On network-level failures.
        """
        params: dict[str, str] = {"page-size": str(limit)}

        if scope_dn:
            url = f"{self._base}/api/mo/{scope_dn}.json"
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
        if time_range:
            params["time-range"] = time_range
        if page is not None:
            params["page"] = str(page)
        if config_only:
            params["rsp-prop-include"] = "config-only"

        logger.debug("GET %s params=%s", url, params)
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

        resp.raise_for_status()

        try:
            body = resp.json()
        except ValueError as exc:
            raise ApicResponseError(url, f"response is not valid JSON: {exc}") from exc

        if "imdata" not in body:
            raise ApicResponseError(url, "response body missing 'imdata' key")

        objects: list[dict[str, Any]] = []
        for item in body.get("imdata", []):
            for cls, obj in item.items():
                attrs: dict[str, Any] = dict(obj.get("attributes", {}))
                attrs["_class"] = cls
                if include_children and "children" in obj:
                    children: list[dict[str, Any]] = []
                    for child_item in obj["children"]:
                        for child_cls, child_obj in child_item.items():
                            child_attrs = dict(child_obj.get("attributes", {}))
                            child_attrs["_class"] = child_cls
                            children.append(child_attrs)
                    attrs["_children"] = children
                objects.append(attrs)

        logger.debug("query_class(%s) → %d objects", class_name, len(objects))
        return objects

    async def _request_json(
        self, url: str, params: dict[str, str]
    ) -> dict[str, Any]:
        """Issue an authenticated GET and return the parsed APIC JSON body.

        Shared by get_by_dn() and count_class().  Performs the same 401/403
        re-authenticate-and-retry and JSON/imdata validation as query_class(),
        but is kept as a self-contained helper so those endpoints do not alter
        the established query_class() code path.

        Args:
            url:    Absolute APIC URL to GET.
            params: Query-string parameters.

        Returns:
            The decoded response body — a dict guaranteed to carry an "imdata" key.

        Raises:
            ApicConnectionError: Host unreachable or request timed out.
            ApicAuthError:       Still unauthorized after re-authentication.
            ApicResponseError:   Body is not valid JSON or lacks 'imdata'.
            httpx.HTTPStatusError: Any other non-2xx APIC response.
        """
        logger.debug("GET %s params=%s", url, params)
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

        resp.raise_for_status()

        try:
            body = resp.json()
        except ValueError as exc:
            raise ApicResponseError(url, f"response is not valid JSON: {exc}") from exc

        if "imdata" not in body:
            raise ApicResponseError(url, "response body missing 'imdata' key")

        return body

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
            ApicConnectionError / ApicAuthError / ApicResponseError on transport
            or protocol failures (see _request_json).
        """
        url = f"{self._base}/api/mo/{dn}.json"
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
            if include_children and "children" in obj:
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

        Uses the APIC `rsp-subtree-include=count` mechanism: the response carries
        a single `moCount` managed object whose attribute holds the tally, so a
        "how many BDs/EPGs/subnets?" question costs one small request instead of
        fetching every object.  Filtering and scoping work exactly as in
        query_class().

        Args:
            class_name:  ACI class to count, e.g. "fvBD".
            filters:     Attribute equality filters {attr: value}.
            scope_dn:    Optional parent DN to scope the count to a subtree.
            filter_expr: Raw APIC filter string, combined with `filters` via and().

        Returns:
            The number of matching objects as an int.

        Raises:
            ApicConnectionError / ApicAuthError / ApicResponseError on transport
            or protocol failures (see _request_json).
        """
        params: dict[str, str] = {"rsp-subtree-include": "count"}
        if scope_dn:
            url = f"{self._base}/api/mo/{scope_dn}.json"
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
        count = self._extract_count(body)
        logger.debug("count_class(%s) → %d", class_name, count)
        return count

    @staticmethod
    def _extract_count(body: dict[str, Any]) -> int:
        """Extract the integer tally from an APIC count response.

        The APIC returns a single `moCount` object whose attributes carry the
        total under `childCount` (or `count` on some builds).  When no moCount
        object is present the top-level `totalCount` is used as a fallback.

        Args:
            body: The decoded APIC response body from a count query.

        Returns:
            The tally as an int, or 0 when it cannot be determined.
        """
        for item in body.get("imdata", []):
            mo = item.get("moCount")
            if mo:
                attrs = mo.get("attributes", {})
                for key in ("childCount", "count"):
                    if key in attrs:
                        try:
                            return int(attrs[key])
                        except (TypeError, ValueError):
                            pass
        try:
            return int(body.get("totalCount"))
        except (TypeError, ValueError):
            return 0

    async def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.aclose()
