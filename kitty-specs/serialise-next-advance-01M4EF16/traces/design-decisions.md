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
- **[2026-10-08][research] Adjudicated design after the architect+implementer squad.**
  Lock-scope divergence resolved from source toward the architect lens: a SHORT
  commit-span per-run-dir lock (`<run_dir>/state.json.lock` via
  `kernel.locks.machine_file_lock`) + a bootstrap-anchored expected-`issued_step_id`
  CAS threaded into plan/commit — NOT a lock held across the composition executor
  (that would breach the 10s `_MAX_HOLD_DEFAULT`/NFR-002 ceiling and risk the 60s
  stale-reclaim). The CAS closes the bootstrap→plan window; the lock only atomizes
  the commit RMW.
- **[2026-10-08][research] Lock must cover FOUR writers**, not two: `_commit_advance`,
  `provide_decision_answer`, the retrospective rollback (`runtime_bridge.py:1111/1121`),
  and (unique-temp fix only, no lock) run-start `_write_snapshot`. The rollback writer
  is deleted by the unmerged sibling `issue-5883-*`; on current main it is live, so we
  cover it, and drop that coverage if the sibling lands first.
- **[2026-10-08][research] Engine path refuses + catches `LockAcquireTimeout`** (not
  just `StaleAdvancePlan`); refusal reuses `kind=blocked`+`reason`+exit 1 — no new
  kind/exit code. Short 4.x ADR to record the run-dir writer model.
- **[2026-10-08][research] Acceptance gate = all THREE red-first repros** (#5682
  composition window, #5854 engine double-apply, answer-vs-next) + the inverted pin.
  Biggest risk is scope under-reach: fixing only `_dn_advance_engine` leaves #5682 and
  the answer race live.
