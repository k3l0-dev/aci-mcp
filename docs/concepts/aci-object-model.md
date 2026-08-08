# ACI Object Model — Concepts for Non-Network Engineers

This page explains the Cisco ACI data model just enough to understand what `niwashi-mcp` does and why its tools are designed the way they are. No prior network or ACI experience required.

---

## What is Cisco ACI?

**Cisco ACI** (Application Centric Infrastructure) is a software-defined networking platform built around a Cisco controller called the **APIC** (Application Policy Infrastructure Controller). The APIC manages the entire network fabric — switches, policies, endpoints — through a REST API.

Every configurable object in ACI — a tenant, a network segment, a security policy, a physical port — is a node in a tree called the **Management Information Tree (MIT)**. On APIC 6.0(9c), the release this server's catalogue is built from, the MIT has **15,452** node types (called *classes*).

---

## Class names

Each class has a compact name formed of two parts:

```text
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
| `faultInst` | `fault` | `Inst` | Active fault (operational data) |
| `fabricNode` | `fabric` | `Node` | Physical switch in the fabric |

Class names are matched **exactly and case-sensitively**: `fvBd` is not `fvBD`, and the lookup returns nothing rather than guessing. Take the name from a `search_classes()` result rather than retyping it from memory.

The 15,452 classes include thousands of abstract base classes, internal relation objects, and monitoring classes — most are never queried directly. Measured on the catalogue: **1,954** are abstract, **3,065** follow the Rs/Rt relation-object naming pattern, and **4,769** carry a stats/telemetry time-bucket suffix. Each category alone is in the thousands.

---

## Two class counts: 15,452 and 15,239

Both numbers are correct, and they answer different questions.

| Count | What it is |
|---|---|
| **15,452** | Classes in the catalogue. These are the classes `get_schema()` can describe and `query()` / `count()` accept — the *validatable* universe. |
| **15,239** | Classes in the search index. These are the classes `search_classes()` can return — the *findable* universe. |

The 213-class difference is a property of the index, not a gap in coverage: those classes have no label, no comment, and no discriminating property label, so there is no text to index them by. A keyword search cannot reach them, but they behave exactly like any other class once you name one directly.

If you have a class name from a design, a DN, or a `contains` list, use it — do not conclude it is invalid because `search_classes()` did not surface it.

---

## Distinguished Names (DN)

Every object in the MIT has a unique path called a **Distinguished Name (DN)**. The DN encodes the full containment path from the root:

```text
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

The DN pattern for a class is shown in `get_schema()` under `rnFormat` (the last component) and `dnFormats` (the full template). Quote those templates verbatim rather than reconstructing a DN from memory — see [`get_schema`](../tools/get_schema.md#reading-dnformats).

---

## Containment hierarchy

Objects are nested — every object has a parent. The tenant is the primary administrative boundary:

```text
Tenant (fvTenant)
├── VRF (fvCtx)              — Layer 3 routing domain
├── Bridge Domain (fvBD)     — Layer 2 segment (linked to one VRF)
│   └── Subnet (fvSubnet)    — IP prefix on the BD
└── Application Profile (fvAp)
    └── EPG (fvAEPg)         — group of endpoints with shared policy
        └── Contract (via relation fvRsCons / fvRsProv)
```

This is why `scope_dn` matters in `query()`: fetching all Bridge Domains in a specific tenant is much faster than a fabric-wide class scan when you pass `scope_dn="uni/tn-OT"`.

---

## Relation classes (Rs/Rt)

ACI uses a special type of object to model relationships between objects. These are named with `Rs` (resolution source) or `Rt` (relation target) in the middle:

| Relation class | Connects | Direction |
|---|---|---|
| `fvRsCtx` | BD → VRF | "this BD is in this VRF" |
| `fvRsCons` | EPG → Contract | "this EPG consumes this contract" |
| `fvRsProv` | EPG → Contract | "this EPG provides this contract" |

These are internal plumbing — you rarely query them directly. The `search_classes()` algorithm applies a structural penalty to Rs/Rt classes (−8, applied after the text score) so they do not crowd out the canonical objects in search results — see [internals/search-algorithm.md](../internals/search-algorithm.md).

What matters when you *do* read one: the Rs object records the target that was **configured**, and that record outlives the target being deleted or renamed. A populated `tnFvCtxName` or `tDn` is therefore not evidence that the target exists — only the relation's `state` property is.

---

## The APIC REST API

The APIC exposes two query patterns used by `niwashi-mcp`:

**Class query** — fetch all objects of a type across the fabric:

```text
GET /api/class/fvBD.json
```

**Subtree query** — fetch objects of a type under a specific DN:

```text
GET /api/mo/uni/tn-OT.json?query-target=subtree&target-subtree-class=fvBD
```

Both accept filter parameters (`query-target-filter`, `order-by`, `page-size`, etc.) that the `query()` tool builds from its arguments.

---

## Why 15,452 classes?

The ACI object model is extremely granular:

- Every configurable knob on a switch policy is its own class
- Abstract base classes exist at every level of the hierarchy
- Relation objects (`Rs`/`Rt`) double the count for every relationship
- Monitoring, fault, and audit objects exist for every configurable class

Of the 15,452 classes, only **3,010** (~19%) correspond to objects a network engineer would directly create or modify. The `isConfigurable` field in `get_schema()` identifies them, and `search_classes()` boosts them in its ranking for the same reason.

---

## Where the model comes from

The server does not ask the APIC what its object model looks like, and it does not read a schema bundle from disk. It reads a SQLite catalogue embedded in the [`niwaki`](https://pypi.org/project/niwaki/) dependency — one file, installed with the package.

Two consequences worth knowing:

- **The model version is pinned by a dependency, not chosen by the operator.** The catalogue is built from APIC 6.0(9c); the version is logged at startup so a silent `niwaki` upgrade cannot change the object model an agent reasons about without leaving a trace.
- **The catalogue describes the model, never your fabric.** Whether a class exists is answered locally; whether *your* fabric holds any objects of it is answered only by `query()` or `count()` against the APIC.

---

## How this maps to the MCP tools

| Tool | What it solves |
|---|---|
| `search_classes(keyword)` | The ACI class namespace is opaque — `fvBD` is not obvious from "bridge domain". This tool bridges plain English to exact class names. |
| `get_schema(class_name)` | Before querying, you need to know: what attributes exist? what identifies an object? what is the parent? The schema answers all of this without hitting the APIC. |
| `query(class_name, ...)` | Executes the actual APIC query with correct filters, scope, and pagination. |
| `get_by_dn(dn)` | Fetches a single object directly when you already hold its exact DN — skips the discovery sequence. |
| `count(class_name, ...)` | Tallies objects of a class without transferring them — for "how many X?" questions. |
