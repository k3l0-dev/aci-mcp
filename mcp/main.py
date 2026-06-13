# Copyright (c) 2026 Khalid El-Ouiali — Monark AIOPS SRL. All rights reserved.
"""
main.py

Schema-driven FastMCP server for Cisco ACI APIC — v0.2.

Architecture
------------
The server exposes three generic tools that let an LLM navigate the entire
ACI object model without any hardcoded class knowledge:

  search_classes  — discover ACI classes by keyword (label + description)
  get_schema      — inspect identifiers, containment, and relations for a class
  query           — execute a filtered class query against the APIC

All ACI domain knowledge lives in:
  ../data/schemas/                 15 k+ jsonmeta files from the APIC /doc/jsonmeta/ endpoint
  ../data/class-descriptions.json  label + comment index built by schema-collector/gen_descriptions.py

Typical LLM workflow
--------------------
  1. search_classes("bridge domain")
       → learns that fvBD is the relevant class
  2. get_schema("fvBD")
       → sees identifiedBy=["name"], containedBy=["fv:Tenant"],
         relationTo={"fvRsCtx": ...}, properties=[...]
  3. query("fvBD", filters={"name": "servers"}, scope_dn="uni/tn-OT")
       → returns bridge domain objects with all their APIC attributes

Environment variables (read from .env at startup)
--------------------------------------------------
  APIC_HOST        APIC hostname or IP
  APIC_USER        APIC username (default: admin)
  APIC_PASSWORD    APIC password
  APIC_VERIFY_SSL  "true" to enforce TLS verification (default: false)
  MCP_PORT         HTTP port the server listens on (default: 8000)

query() parameters
------------------
  filters              Simple equality filters {attr: value}
  scope_dn             Restrict query to a subtree DN
  limit / page         Pagination (page-size / 0-based page number)
  order_by             e.g. "faultInst.severity|desc"
  include_children     Embed direct children inline, e.g. ["fvSubnet","fvRsCtx"]
                       Equivalent to rsp-subtree=children&rsp-subtree-class=X,Y
  filter_expr          Raw APIC filter: wcard, ne, gt, and/or compositions
                       e.g. 'wcard(fvBD.dn,"uni/tn-OT")'
  rsp_subtree_include  Inline subtrees: "faults", "health", "audit-logs",
                       "faults,required", "faults,no-scoped"
  time_range           Log record window: "24h", "1week", "2026-01-01|2026-01-31"
                       Valid for faultRecord, aaaModLR, eventRecord
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from apic.client import ApicClient
from dotenv import load_dotenv
from exceptions import ConfigurationError, UnknownClassError
from fastmcp import Context, FastMCP
from fastmcp.server.lifespan import lifespan
from middleware.auth import ApiKeyMiddleware, load_api_keys
from middleware.oauth import OAuthDiscoveryMiddleware
from registry.descriptions import load_descriptions
from registry.descriptions import search as desc_search
from registry.schema import load_schema

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
REPO_ROOT = BASE_DIR.parent
SCHEMAS_DIR = REPO_ROOT / "data" / "schemas"
DESCRIPTIONS_FILE = REPO_ROOT / "data" / "class-descriptions.json"
ENV_FILE = REPO_ROOT / ".env"

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("aci-mcp")

# ── Lifespan ──────────────────────────────────────────────────────────────────


@lifespan
async def app_lifespan(server: FastMCP):
    """Load registry and connect to the APIC at startup; close on shutdown.

    Yields a context dict available to all tools via ctx.lifespan_context:
      descriptions  — in-memory class descriptions index
      backend       — ApicClient instance
      schemas_dir   — Path to the jsonmeta schema directory
    """
    load_dotenv(ENV_FILE)

    descriptions = load_descriptions(DESCRIPTIONS_FILE)
    logger.info("Registry loaded — %d class descriptions", len(descriptions))

    host = (
        os.environ.get("APIC_HOST", "")
        .removeprefix("https://")
        .removeprefix("http://")
        .strip()
    )
    if not host:
        raise ConfigurationError(
            "APIC_HOST is not set. Add it to .env or export it before starting the server."
        )

    password = os.environ.get("APIC_PASSWORD", "")
    if not password:
        raise ConfigurationError(
            "APIC_PASSWORD is not set. Add it to .env or export it before starting the server."
        )

    user = os.environ.get("APIC_USER", "admin")
    verify_ssl = os.environ.get("APIC_VERIFY_SSL", "false").lower() == "true"
    backend = ApicClient(host=host, user=user, password=password, verify_ssl=verify_ssl)
    await backend.authenticate()
    logger.info("Connected to APIC — %s", host)

    try:
        yield {
            "descriptions": descriptions,
            "backend": backend,
            "schemas_dir": SCHEMAS_DIR,
        }
    finally:
        await backend.close()
        logger.info("Backend closed")


# ── Server ────────────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="aci-mcp",
    lifespan=app_lifespan,
    instructions="""
You are an assistant for querying a Cisco ACI fabric through its APIC REST API.

MANDATORY WORKFLOW — never deviate from this sequence:

  Step 1 — ALWAYS call search_classes(keyword) first.
    Your training knowledge of ACI class names is unreliable: names vary
    across APIC versions and many classes have similar names (fvAEPg, fvCEp,
    fvStCEp…).  Never assume a class name — always verify it.

  Step 2 — ALWAYS call get_schema(class_name) before query().
    The schema tells you which attributes exist on the class (properties),
    which attributes uniquely identify instances (identifiedBy), and what
    the parent DN looks like (containedBy → use as scope_dn).
    Querying with an attribute that does not exist silently returns nothing.

  Step 3 — Only then call query(class_name, filters, scope_dn).
    Use the "dn" from any result as scope_dn to fetch child objects.

Skipping steps 1 or 2 produces wrong class names, wrong filters, and
empty results.  The search + schema cost is two fast local lookups —
always worth it.
""",
)

# ── Tools ─────────────────────────────────────────────────────────────────────


@mcp.tool
async def search_classes(
    keyword: str,
    ctx: Context,
    limit: int = 10,
) -> list[dict[str, str]]:
    """Search ACI class descriptions by keyword.

    Performs a case-insensitive substring match against three fields:
    class name, human-readable label, and description comment.  Results
    are ranked by relevance (class name match > label > comment).

    Use this tool whenever the exact ACI class name for a concept is not
    known (e.g. "bridge domain", "contract", "fault", "VRF", "node").

    Args:
        keyword: Plain English term or partial ACI class name to search for.
        ctx:     Injected FastMCP context (not a user-facing parameter).
        limit:   Maximum results to return (default 10, capped at 50).

    Returns:
        List of dicts, each with:
          class_name — ACI class name, e.g. "fvBD"
          label      — short human label, e.g. "Bridge Domain"
          comment    — one-sentence description from the APIC schema
    """
    descriptions: dict = ctx.lifespan_context["descriptions"]
    results = desc_search(keyword, descriptions, min(limit, 50))
    await ctx.info(f"search_classes({keyword!r}) → {len(results)} results")
    return results


@mcp.tool
async def get_schema(
    class_name: str,
    ctx: Context,
) -> dict[str, Any]:
    """Return the structural schema for an ACI class.

    Extracts the query-planning fields from the APIC jsonmeta schema file
    for the given class.  The returned dict contains:

      identifiedBy   — attribute(s) that uniquely identify an instance;
                        use these as filter keys in query()
      rnFormat       — relative-name template showing identifier placeholders
      containedBy    — parent class name(s) in "pkg:Class" notation;
                        fetch the parent object and use its dn as scope_dn
      dnFormats      — complete DN pattern examples for this class
      relationTo     — outgoing Rs relations: {relClass: {targetClass, cardinality}}
      relationFrom   — incoming Rt relations: {relClass: {sourceClass}}
      properties     — sorted list of all available attribute names
      isAbstract     — True when the class cannot be directly instantiated
      isConfigurable — True when objects can be created/modified via APIC
      className      — short name without package prefix, e.g. "BD"
      classPkg       — package prefix, e.g. "fv"
      label          — human-readable label

    Args:
        class_name: Exact ACI class name, e.g. "fvBD", "fvAEPg", "faultInst".
        ctx:        Injected FastMCP context (not a user-facing parameter).

    Returns:
        Schema dict as described above, or an empty dict when the class file
        is not found in the local schema collection.
    """
    schemas_dir: Path = ctx.lifespan_context["schemas_dir"]
    schema = load_schema(class_name, schemas_dir)
    if schema:
        await ctx.info(f"get_schema({class_name!r}) → OK")
    else:
        await ctx.warning(f"get_schema({class_name!r}) → not found")
    return schema


@mcp.tool
async def query(
    class_name: str,
    ctx: Context,
    filters: dict[str, str] | None = None,
    scope_dn: str | None = None,
    limit: int = 20,
    order_by: str | None = None,
    include_children: list[str] | None = None,
    filter_expr: str | None = None,
    rsp_subtree_include: str | None = None,
    time_range: str | None = None,
    page: int | None = None,
) -> list[dict[str, Any]]:
    """Query ACI objects of a given class from the APIC.

    ⚠ PREREQUISITE — before calling this tool you MUST have:
      1. Called search_classes(keyword) to verify the exact class name.
      2. Called get_schema(class_name) to learn valid filter attribute names.
    Skipping these steps leads to empty results with no error — the APIC
    silently returns nothing for unknown classes or wrong attribute names.

    Executes a GET against the APIC class or subtree endpoint.
    The APIC filter string is built automatically from `filters`.
    Providing `scope_dn` issues a subtree query rooted at that DN,
    which is faster and more precise than a fabric-wide class scan.

    Args:
        class_name: Exact ACI class name verified via search_classes(),
                    e.g. "fvBD", "faultInst", "fabricNode".
        ctx:        Injected FastMCP context (not a user-facing parameter).
        filters:    Attribute filters as {attribute: value} pairs.
                    Attribute names must match properties from get_schema().
                    Example: {"name": "servers"}  →  eq(fvBD.name,"servers")
                    Multiple entries are combined with APIC and() syntax.
        scope_dn:   DN of a parent object to scope the subtree query.
                    Example: "uni/tn-OT" restricts results to tenant OT.
                    Use the "dn" field from a previous query result.
        limit:            Maximum objects to return (default 20, capped at 200).
        order_by:         APIC ordering expression, e.g. "faultInst.severity|desc".
        include_children: Child class names to embed in each result in one call,
                          e.g. ["fvSubnet", "fvRsCtx"].  Each returned object
                          will contain a "_children" list of child attribute dicts.
                          Equivalent to moquery -x rsp-subtree=children
                          -x rsp-subtree-class=X,Y.

    Returns:
        List of attribute dicts.  Each dict contains all APIC attributes for
        the object plus a "_class" key with the ACI class name.
        The "dn" attribute is always present and encodes the full object path.
        When include_children is set, each dict also contains "_children":
        a list of child attribute dicts, each with their own "_class" key.

    Raises:
        UnknownClassError: class_name is not in the registry — includes closest
                           matches so the LLM can self-correct.
    """
    descriptions: dict = ctx.lifespan_context["descriptions"]
    backend: ApicClient = ctx.lifespan_context["backend"]

    # Validate class_name against the registry — catch typos and wrong names
    # before hitting the backend (which would silently return []).
    if class_name not in descriptions:
        suggestions = desc_search(class_name, descriptions, limit=5)
        suggestion_names = [s["class_name"] for s in suggestions]
        await ctx.warning(f"query called with unknown class {class_name!r}")
        raise UnknownClassError(class_name, suggestion_names, len(descriptions))

    await ctx.info(
        f"query({class_name!r}, filters={filters!r}, scope={scope_dn!r}, limit={limit})"
    )

    results = await backend.query_class(
        class_name=class_name,
        filters=filters or {},
        scope_dn=scope_dn or "",
        limit=min(limit, 200),
        order_by=order_by or "",
        include_children=include_children,
        filter_expr=filter_expr,
        rsp_subtree_include=rsp_subtree_include,
        time_range=time_range,
        page=page,
    )
    await ctx.info(f"query → {len(results)} objects returned")
    return results


# ── Entry point ───────────────────────────────────────────────────────────────


async def _serve() -> None:
    load_dotenv(ENV_FILE)
    _port_raw = os.environ.get("MCP_PORT", "8000")
    try:
        port = int(_port_raw)
    except ValueError:
        raise ConfigurationError(
            f"MCP_PORT must be an integer, got '{_port_raw}'."
        ) from None

    api_keys = load_api_keys()
    from starlette.middleware import Middleware

    # OAuthDiscoveryMiddleware must be outermost (first in list) so it intercepts
    # /.well-known/ discovery paths before ApiKeyMiddleware sees them.
    if api_keys:
        logger.info("API key authentication enabled (%d key(s) loaded)", len(api_keys))
        middleware = [
            Middleware(OAuthDiscoveryMiddleware),
            Middleware(ApiKeyMiddleware, api_keys=api_keys),
        ]
    else:
        logger.warning(
            "MCP_API_KEYS is not set — server is running WITHOUT authentication. "
            "Set MCP_API_KEYS in .env before deploying to production."
        )
        middleware = [Middleware(OAuthDiscoveryMiddleware)]

    await mcp.run_http_async(
        host="0.0.0.0",
        port=port,
        stateless_http=True,
        json_response=True,
        middleware=middleware,
    )


if __name__ == "__main__":
    asyncio.run(_serve())
