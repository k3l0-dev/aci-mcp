# Supported APIC releases

`niwashi-mcp` ships **one** ACI object model at a time. It travels inside the
[`niwaki`](https://pypi.org/project/niwaki/) dependency as a single SQLite
catalogue, which is what makes `uvx niwashi-mcp` work with no download and no
data directory.

| Currently shipped | Classes | Source |
|---|---:|---|
| **APIC 6.0(9c)** | 15,452 | `niwaki>=1.8,<1.9` |

The server logs the catalogue's release at startup:

```text
Registry loaded — 15239 class descriptions (niwaki catalogue, APIC 6.0(9c))
```

---

## Running a different release

Most of it still works. The object model is stable across trains and the
overwhelming majority of classes are identical between them.

What does not work is narrow but worth knowing: **classes added, removed or
renamed in your release are missing or wrong, and the failure is quiet.** The
APIC does not return an error for a class it does not know — it returns an empty
result, which is indistinguishable from "there are none".

So an empty result on a class you are confident exists is the symptom to watch
for. It is the one case where the version gap can mislead you.

---

## Requesting a release

Add a row below in a pull request. That is the whole contribution — no data, no
export from your controller, nothing that leaves your fabric.

| APIC release | Requested | By |
|---|---|---|
| _e.g. 6.1(2f)_ | _2026-08-09_ | _@your-handle_ |

Demand decides order. When a release has askers, a catalogue for it is built and
published in `niwaki`, and `niwashi-mcp` picks it up in its next release. The
pull request is where you will get a realistic timing rather than an open-ended
promise.

If you hit a case where the version gap produced a **wrong** answer rather than
an empty one, please open an issue instead. That is a worse failure than the one
described above and has not been observed.
