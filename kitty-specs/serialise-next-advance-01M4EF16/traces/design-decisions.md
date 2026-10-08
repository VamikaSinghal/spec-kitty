# Design Decisions — serialise-next-advance

Running log of design choices and their rationale. Append dated entries.

## Decisions carried in from research (to be ratified in spec/plan)

- **[2026-10-08][research] Structural fix over a symptomatic patch.** The target
  invariant is: "Advancing a run is serialised: a stale advance is refused with a
  named reason, never silently re-planned as success; at most one `next` mutates a
  run at a time." Prefer closing the defect class by construction (a locked,
  compare-and-swap advance) over patching one path.
- **[2026-10-08][research] Refusal must fit the existing `next` JSON contract.**
  A stale/contended advance should surface as the existing `Decision kind=blocked`
  + `reason` with no new kind and no new exit code (composition's EDGE-003 shape).
  If it cannot, that is an owner/ADR decision point.
- **[2026-10-08][research] Lock-ordering caution.** `get_or_start_run` already
  takes the run-index lock during bootstrap; a new per-run-dir lock must not
  deadlock against it. `STALE_AFTER_S_DEFAULT`=60s vs the ~10s hold ceiling bounds
  how long an advance may hold the lock (composition dispatch runs executors).
- **[2026-10-08][research] Fold the `provide_decision_answer` race** (no issue yet;
  file one or cover it in the PR). Do NOT fold #5112 (livelock), #5099, #5853, #5855.
  Residual: a refusing stale path can make loops spin on `blocked` (#5112).
