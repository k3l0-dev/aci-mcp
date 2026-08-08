# Baseline — the drift net for the 2.0 migration

## Why this exists

The 2.0 release swaps the data layer: raw `jsonmeta` files become niwaki's
SQLite catalogue. **The five tools keep their signatures**, so a defect in that
swap is silent by construction — a changed field shape, a drifted ranking, a
truncated list. Every pre-existing test would still pass.

Two concrete gaps this closes:

- `tests/eval/test_search_quality.py` enforces **floors** of 60 % / 85 %, while
  the implementation actually delivers **78.4 % / 94.6 %**. A regression to 61 %
  was, until now, a green build.
- Nothing anywhere pinned the output of `get_schema()`. It is the tool an agent
  relies on most, and it was the least protected.

## What is recorded

`baseline.json` (≈220 KB, committed) holds:

| Section | Content | Asserted? |
|---|---|---|
| `index` | class count, content digest, field names | yes, equality |
| `schemas` | `get_schema()` for 38 stratified classes — digest of the **full** output, plus counts and a readable excerpt | yes, equality |
| `search` | R@1 / R@5 / MRR overall and per tier, **plus the exact top-5 of all 74 golden queries** | yes, equality |
| `perf` | observed timings on the hot paths | **no** — see below |

The sample of 38 classes is stratified, not random. Each entry is there because
it exercises a shape the swap could plausibly break: `Rs`/`Rt` relations and
their colon notation, abstract classes, stats classes, enum-heavy classes, the
huge-`dnFormats` monsters (`faultDelegate` at 64,313 templates), and one class
that must stay absent so the empty-dict contract stays pinned.

**Digests are always computed on the full schema.** Large payloads are trimmed
for storage only — drift detection is unaffected.

## Why timings are recorded but not asserted

A CI runner is not a workstation. Wall-clock equality across machines produces
false failures, and a test that cries wolf gets muted — which is worse than no
test. Enforcement stays where it belongs, in `tests/perf/`, which asserts
budgets rather than equality. The numbers here let a human spot an
order-of-magnitude regression at a glance.

## Usage

```bash
# Run the net (part of the default suite)
uv run pytest tests/baseline/

# Compare without writing anything
python -m tests.baseline.capture --check

# Re-record the reference
python -m tests.baseline.capture
```

## Re-recording is a deliberate act

Regenerating `baseline.json` to make a red build go green defeats the entire
purpose of this directory. Re-record **only** when the change in behaviour is
intended, and **say so in the commit message**, with the reason. A reviewer
must be able to see that the movement was chosen rather than absorbed.

## What this does *not* cover

Agent-level behaviour. Whether an LLM still reaches the right answer using
these tools is measured by the separate `agent-eval/` harness, which is not part
of this repository. This net covers the deterministic layer underneath: if the
data the agent receives is identical, agent behaviour has no reason to move —
but that inference is not a proof, and the agent harness remains the place where
it is tested.

## Reference values at capture time

Recorded on the pre-2.0 (jsonmeta) data layer, APIC 6.0(9c) corpus:

```
index      15,239 classes
search     R@1 78.4 %   R@5 94.6 %   MRR 0.846   (n=74)
  tier 1   n=44   R@1  91 %   R@5 100 %
  tier 2   n=10   R@1 100 %   R@5 100 %
  tier 3   n=15   R@1  53 %   R@5  80 %
  tier 4   n= 5   R@1   0 %   R@5  80 %
schemas    38 classes sampled
```

Note: MRR is 0.846 here against 0.854 quoted elsewhere in the project docs. The
difference is the cut-off — this harness ranks within the top 5, the older
figure was computed over a longer result list. Both are correct for their own
definition; this file is self-consistent and is the reference the tests use.
