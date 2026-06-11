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
            httpx.HTTPStatusError: When the APIC returns a non-2xx status.
            httpx.RequestError:    On network-level failures.
        """
        payload = {"aaaUser": {"attributes": {"name": self._user, "pwd": self._password}}}
        resp = await self._client.post(f"{self._base}/api/aaaLogin.json", json=payload)
        resp.raise_for_status()
        token: str = resp.json()["imdata"][0]["aaaLogin"]["attributes"]["token"]
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

        logger.debug("GET %s params=%s", url, params)
        resp = await self._client.get(url, params=params)

        if resp.status_code in (401, 403):
            logger.warning("APIC returned %d — re-authenticating and retrying", resp.status_code)
            await self.authenticate()
            resp = await self._client.get(url, params=params)

        resp.raise_for_status()

        objects: list[dict[str, Any]] = []
        for item in resp.json().get("imdata", []):
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

    async def close(self) -> None:
        """Release the underlying HTTP connection pool."""
        await self._client.aclose()
