# ACI Object Model — Concepts for Non-Network Engineers

This page explains the Cisco ACI data model just enough to understand what `aci-mcp` does and why the three tools are designed the way they are. No prior network or ACI experience required.

---

## What is Cisco ACI?

**Cisco ACI** (Application Centric Infrastructure) is a software-defined networking platform built around a Cisco controller called the **APIC** (Application Policy Infrastructure Controller). The APIC manages the entire network fabric — switches, policies, endpoints — through a REST API.

Every configurable object in ACI — a tenant, a network segment, a security policy, a physical port — is a node in a tree called the **Management Information Tree (MIT)**. The MIT has over 15 000 node types (called *classes*).

---

## Class names

Each class has a compact name formed of two parts:

```
fv  BD
│   │
│   └── short name: BD (Bridge Domain)
└── package prefix: fv (fabric virtualization)
```

| Class | Package | Short name | Meaning |
|---|---|---|---|
| `fvTenant` | `fv` | `Tenant` | Top-level administrative container |
| `fvBD` | `fv` | `BD` | Bridge Domain (a Layer 2 segment) |
| `fvCtx` | `fv` | `Ctx` | VRF (a Layer 3 routing domain) |
| `fvAEPg` | `fv` | `AEPg` | Application Endpoint Group |
| `vzBrCP` | `vz` | `BrCP` | Contract (security policy between EPGs) |
| `faultInst` | — | — | Active fault (operational data) |
| `fabricNode` | `fabric` | `Node` | Physical switch in the fabric |

The 15 000+ total classes include hundreds of abstract base classes, internal relation objects, and monitoring classes — most are never queried directly.

---

## Distinguished Names (DN)

Every object in the MIT has a unique path called a **Distinguished Name (DN)**. The DN encodes the full containment path from the root:

```
uni/tn-OT/BD-servers
│   │      │
│   │      └── BD object named "servers"
│   └── Tenant named "OT"
└── root of the MIT
```

More examples:

| DN | Object |
|---|---|
| `uni` | MIT root |
| `uni/tn-OT` | Tenant "OT" |
| `uni/tn-OT/ctx-prod` | VRF "prod" inside tenant "OT" |
| `uni/tn-OT/BD-servers` | Bridge Domain "servers" inside tenant "OT" |
| `uni/tn-OT/BD-servers/subnet-[10.0.1.0/24]` | Subnet inside the BD |
| `topology/pod-1/node-101` | Leaf switch 101 in pod 1 |

The DN pattern for a class is shown in `get_schema()` under `rnFormat` and `dnFormats`.

---

## Containment hierarchy

Objects are nested — every object has a parent. The tenant is the primary administrative boundary:

```
Tenant (fvTenant)
├── VRF (fvCtx)              — Layer 3 routing domain
├── Bridge Domain (fvBD)     — Layer 2 segment (linked to one VRF)
│   └── Subnet (fvSubnet)    — IP prefix on the BD
└── Application Profile (fvAp)
    └── EPG (fvAEPg)         — group of endpoints with shared policy
        └── Contract (via relation fvRsConsumedBrCP / fvRsProvided)
```

This is why `scope_dn` matters in `query()`: fetching all Bridge Domains in a specific tenant is much faster than a fabric-wide class scan when you pass `scope_dn="uni/tn-OT"`.

---

## Relation classes (Rs/Rt)

ACI uses a special type of object to model relationships between objects. These are named with `Rs` (resolution source) or `Rt` (relation target) in the middle:

| Relation class | Connects | Direction |
|---|---|---|
| `fvRsCtx` | BD → VRF | "this BD is in this VRF" |
| `fvRsConsumedBrCP` | EPG → Contract | "this EPG consumes this contract" |
| `fvRsProvidedBrCP` | EPG → Contract | "this EPG provides this contract" |

These are internal plumbing — you rarely query them directly. The `search_classes()` algorithm penalizes them (−3 score) so they do not crowd out the canonical objects in search results.

---

## The APIC REST API

The APIC exposes two query patterns used by `aci-mcp`:

**Class query** — fetch all objects of a type across the fabric:
```
GET /api/class/fvBD.json
```

**Subtree query** — fetch objects of a type under a specific DN:
```
GET /api/mo/uni/tn-OT.json?query-target=subtree&target-subtree-class=fvBD
```

Both accept filter parameters (`query-target-filter`, `order-by`, `page-size`, etc.) that the `query()` tool builds from its arguments.

---

## Why 15 000+ classes?

The ACI object model is extremely granular:

- Every configurable knob on a switch policy is its own class
- Abstract base classes exist at every level of the hierarchy
- Relation objects (`Rs`/`Rt`) double the count for every relationship
- Monitoring, fault, and audit objects exist for every configurable class

Of the 15 000+ classes, only a few hundred correspond to objects a network engineer would directly create or modify. The `isConfigurable` field in `get_schema()` identifies them.

---

## How this maps to the three MCP tools

| Tool | What it solves |
|---|---|
| `search_classes(keyword)` | The ACI class namespace is opaque — `fvBD` is not obvious from "bridge domain". This tool bridges plain English to exact class names. |
| `get_schema(class_name)` | Before querying, you need to know: what attributes exist? what identifies an object? what is the parent? The schema answers all of this without hitting the APIC. |
| `query(class_name, ...)` | Executes the actual APIC query with correct filters, scope, and pagination. |
