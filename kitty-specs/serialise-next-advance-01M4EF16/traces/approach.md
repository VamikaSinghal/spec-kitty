# Approach — serialise-next-advance

Running log of how the approach evolves. Append dated entries; do not rewrite history.

## Initial approach (from the operator brief + pre-spec research)

- **[2026-10-08][research] Two-layer root cause confirmed on main c0a08ad6.**
  (1) The advance never records which step its caller evaluated: bootstrap reads
  `snapshot.issued_step_id` (runtime_bridge.py ~551) but `plan_advance`
  re-reads `state.json` (engine.py:429) and `commit_advance`'s stale check
  (engine.py:599-627) only covers that *second* read, not the bootstrap→plan
  window — the #5682 mechanism on both the composition and engine paths.
  (2) No mutual exclusion covers read→plan→write on a run dir; `_write_snapshot`
  uses a fixed `state.json.tmp` (engine.py:129-140); `provide_decision_answer`
  (engine.py:797-873) is a third unlocked writer.
- **[2026-10-08][research] Chosen direction (to be validated by the post-spec/post-plan squads):**
  (a) thread the bootstrap-evaluated `issued_step_id` into plan+commit and refuse on
  mismatch, on both paths; (b) hold a per-run-dir `kernel.locks.machine_file_lock`
  around the read→plan→write span (exemplar: `run_index.py`), covering
  `provide_decision_answer`, with a unique temp name in `_write_snapshot`;
  (c) make `_dn_advance_engine` refuse a stale plan with a `blocked` Decision +
  named reason (like composition's EDGE-003), instead of the `next_step` fallback.
- **[2026-10-08][research] Constraint:** the runtime layer cannot import
  `specify_cli` (layer rules), so PR #5890's `mission_write_lock` is not reusable;
  `kernel.locks` is the only lock door (gated by `test_lock_primitive_ban.py`).
