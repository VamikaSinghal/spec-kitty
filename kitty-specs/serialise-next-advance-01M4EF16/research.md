# Research — serialise-next-advance

> Mission: `serialise-next-advance-01M4EF16`
> Closes: #5854 (P2, epic #3897), #5682 (P1, epic #3897)
> Verified against `main` @ `c0a08ad6` (2026-10-08). Line numbers are current-main.

## Target invariant

**Advancing a run is serialised: a stale advance is refused with a named reason,
never silently re-planned as success; at most one `next` mutates a run at a
time.** Prefer a structural fix (close the defect class by construction) over a
per-path patch.

## Root cause — one cause, two layers

### Layer 1 — the advance does not record which step its caller evaluated

- At bootstrap, `src/runtime/next/runtime_bridge.py:549-553` reads
  `snapshot.issued_step_id` into `current_step_id`. The gates use that value:
  the dependency / WP-iteration gate and composition dispatch
  (`_dn_composition_dispatch`, `runtime_bridge.py:1009`) derive `composed_action`
  from that bootstrap step.
- `plan_advance` (`src/runtime/next/_internal_runtime/engine.py:410`) then
  **re-reads** `state.json` (`engine.py:429`) and plans from that second read;
  `apply_result` (`engine.py:277-301`) completes whatever `snapshot.issued_step_id`
  is at that second read.
- `plan.source` records the **second** read, so `commit_advance`'s stale check
  (`engine.py:599-627`, `_read_snapshot() != plan.source`) only covers
  plan→commit — never bootstrap→plan. A peer `next` landing between the bootstrap
  read and `plan_advance`'s read is undetected, and the call completes the step
  issued at the second read. **This is exactly the #5682 mechanism, on both
  paths.**
- Composition path: `_dn_composition_dispatch` → `_dn_plan_composition_advance`
  (`runtime_bridge.py:950-963`, calls `plan_advance`) →
  `runtime_bridge_engine.advance_run_state_after_composition`
  (`runtime_bridge_engine.py:209-270`) → `commit_advance`. Built-in software-dev
  `implement`/`review` route through composition, so the reported repro most
  likely took this path (inferred, not run).

### Layer 2 — no mutual exclusion covers read→plan→write on a run directory

- `commit_advance`'s compare-then-write (`engine.py:624`) is a TOCTOU: two writers
  can both pass the `== plan.source` check, both append
  `NextStepAutoCompleted`/`NextStepIssued`, and last-`state.json`-write wins.
- `_write_snapshot` (`engine.py:129-140`) uses a **fixed** `state.json.tmp`, so two
  writers collide on the temp file.
- `_append_event` (`engine.py:104-120`) only *comments* "per-run, single-writer";
  nothing enforces it.
- `provide_decision_answer` (`engine.py:797-873`) is a **third** unlocked
  `state.json` writer, which a `next --answer` can race. **Fold this in.**

### #5854's second half — the engine path re-plans a stale advance as success

- `_dn_advance_engine` (`runtime_bridge.py:1234-1248`) catches `StaleAdvancePlan`
  and falls back to `runtime_next_step(result=ctx.result)`, which re-applies
  `success` and can complete a step this caller never ran. Its own docstring says
  so and names #5854. The composition path refuses instead (EDGE-003 `blocked`).

## Lock primitive

Use `kernel.locks.machine_file_lock` (`SyncMachineFileLock`; sync, cross-process,
fcntl/msvcrt; `blocking`/`timeout_s`/`reentrant`; `STALE_AFTER_S_DEFAULT`=60s). It
is the single door for raw locks, gated by
`tests/architectural/test_lock_primitive_ban.py`.

- Exemplar: `src/runtime/next/run_index.py:124-131` (#5389/#5390, PR #5487) — a
  dedicated `.lock` sidecar, a blocking acquire with a 10s timeout
  (`_LOCK_TIMEOUT_S`), and a re-read inside the lock (`index_lock(repo_root)`).
- PR #5890's `mission_write_lock` is on main
  (`src/specify_cli/status/mission_write.py`) but the runtime layer **cannot
  import `specify_cli`** (layer rules), so it does not apply here.
- Watch `STALE_AFTER_S_DEFAULT`=60s (stale-lock adoption) against how long a
  `next` can hold the lock (composition dispatch runs step-contract executors).
- Check lock ordering against the run-index lock taken in `get_or_start_run`
  (`runtime_bridge.py:531`, during bootstrap).
- `kernel/atomic.py` offers a unique-temp atomic write (`atomic_write`).

## Recommended design (researcher lens — validated/refined by the squad below)

- **(a)** Thread the bootstrap-evaluated step id into plan and commit, and refuse
  on mismatch, on both paths.
- **(b)** Hold a per-run-dir `machine_file_lock` around re-validate + append +
  replace (preferably bootstrap→commit), including `provide_decision_answer`. Use
  a unique temp name.
- **(c)** Make `_dn_advance_engine` refuse a stale plan with a `blocked` Decision
  and a named reason, as composition's EDGE-003 does, instead of falling back to
  `next_step`.
- Verify the refusal fits the existing `next` JSON contract (`kind=blocked` +
  `reason`) with no new kind and no new exit code. If it cannot, that is an owner
  decision.

## Tests and prior art

- `tests/runtime/test_run_index_portability_regression.py:130-230` — two
  deterministic race patterns: a forked `mp.Barrier`, and a monkeypatched-loader
  interleave with a `threading.Barrier`.
- Most deterministic #5682 red-first test: monkeypatch
  `_composition._dispatch_via_composition` (or `_guards._should_advance_wp_step`)
  so that, mid-call, it commits a peer advance through `next_step`; assert the
  original call refuses. The engine path gets the same treatment.
- `tests/next/test_runtime_bridge_unit.py` ~2442-2465
  (`test_pre_resolution_is_not_reused_for_a_different_issued_step`) PINS the
  stale→`next_step` fallback. **Invert** it so a stale plan returns `blocked`; do
  not delete it.
- `tests/next/test_engine_commit_primitives.py` ~209 (a stale plan writes nothing)
  is reusable as-is.
- `docs/architecture/runtime-loop.md:54` advertises "multiple agents … in
  parallel". Update it to state the guarantee that ships.
- No ADR covers the run-dir writer model; probably add one (a new cross-process
  locking model is a possible owner-decision point).
- `kitty-specs/composition-advance-alignment-01M49EKF/research.md:68` already
  scopes #5854 as "a run-dir lock, and a refusing engine path".

## Siblings & scope

- **Do NOT fold #5112** (livelock guard) — name it as a residual: a refusing
  stale path can make loops spin on `blocked`.
- **Do NOT fold** #5099 (one writer per checkout), #5853, #5855.
- **DO fold** the `provide_decision_answer` race (no issue yet; file one or cover
  it in the PR).

## Coordination (do not collide)

- Branch `issue-5883-mission-writer-followups` (tip `0c938174`, another runner,
  closing #5883/#5884/#5885) is **unmerged and has no open PR** as of 2026-10-08.
  It heavily rewrites `engine.py` `next_step` (adds `before_run_completed` — note:
  already present on current main), `runtime_bridge.py` `_dn_advance_engine` /
  `_dn_decision_materialize` (new signature, deletes the speculative-rollback
  `_dn_capture_pre_speculative_state` / `STATE_FILE`), `runtime_bridge_engine.py`
  (`_TerminalRetrospective`→`TerminalRetrospective`),
  `runtime_bridge_retrospective.py`, `kernel/atomic.py`, `mission_write.py`.
  While unmerged: keep engine-level work (lock, expected-step CAS,
  `provide_decision_answer`) in `engine.py` / `run_index`-style helpers; keep
  call-site edits in `runtime_bridge*.py` minimal; merge origin/main as soon as it
  lands (never rebase once a PR exists).
- PR #5896 (Ivan-Melck) touches commit-scope code; stay out of it.
- PR #5926 (open) touches `next_cmd.py`/`safe_commit`/`commit_helpers.py` in the
  `specify_cli` layer — not the runtime-layer target; minimal overlap.

## Open questions for specify/plan

1. Lock scope: whole bootstrap→commit span under one lock, vs. a lock + an
   expected-`issued_step_id` compare-and-swap passed into `plan_advance`/
   `commit_advance`. (Squad adjudication below.)
2. Lock ordering vs. the run-index lock in `get_or_start_run` — any deadlock?
3. Does run START (`MissionRunStarted` / `get_or_start_run`) write `state.json`
   and must it take the same per-run-dir lock?
4. Does the refusal fit the existing `next` JSON contract with no new kind / exit
   code? (If not → owner/ADR decision.)
5. Is a new cross-process run-dir writer-model ADR warranted?

## Architect lens (adversarial squad — architect-alphonso)

_To be appended when the squad returns._

## Implementer lens (adversarial squad — implementer-ivan)

_To be appended when the squad returns._
