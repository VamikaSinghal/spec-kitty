# Mission Specification: Serialise concurrent next advance

**Mission Branch**: `issue-5854-serialise-next`
**Created**: 2026-10-08
**Status**: Draft
**Input**: Serialise concurrent `spec-kitty next` on one mission run; a stale advance must be refused with a named reason, never silently re-planned as success (closes #5854, #5682).

## Context

`spec-kitty next` advances a mission run by reading the run cursor
(`issued_step_id`), evaluating gates against it, then committing the advance.
Under the documented multi-agent loop ("multiple agents working on the same
mission in parallel") — or one agent retrying a slow call — two advances overlap
and the run's single-writer assumption, stated only in a code comment, is
violated:

- The cursor is read once at bootstrap, but the commit re-reads `state.json` and
  completes whatever step is issued *at that second read*; nothing compares the
  two reads, so the caller can complete a step it never evaluated (#5682).
- On a stale plan, the engine path re-plans through `next_step` and re-applies
  `success`, double-completing a step (#5854); the composition path already
  refuses.
- No lock covers the read→modify→write of a run cursor; `provide_decision_answer`
  is a third unguarded writer.

The result observed in the field: a review step marked done while its work
package sits in `for_review`, the loop reports `terminal`, and recovery is
manual. This mission makes advancing a run serialised.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Overlapping advances never complete an unevaluated step (Priority: P1)

An orchestrator (or two agents) runs `spec-kitty next --result success` twice,
overlapping, on one run. The run must never complete a step whose gates nobody
evaluated.

**Why this priority**: This is the #5682 data-loss symptom — a lost review step
wedges the mission (`accept` fails closed, no CLI rewinds the cursor). It is the
mission's core value.

**Independent Test**: A deterministic, no-sleep reproduction injects a peer
advance synchronously in the window between the first caller's bootstrap read and
its own plan, on both the composition and engine paths, and asserts the first
caller is refused (not silently completing the peer's step).

**Acceptance Scenarios**:

1. **Given** a run with the cursor at `implement` and WP01 in `for_review`,
   **When** a peer advance moves the cursor to `review` after this caller's
   bootstrap read but before its commit, **Then** this caller is refused with a
   named reason and does not complete `review`.
2. **Given** no overlapping advance, **When** a single `next --result success`
   runs, **Then** it completes the issued step and issues the next one exactly as
   today (positive control).

---

### User Story 2 - The engine path refuses a stale advance (Priority: P1)

**Why this priority**: The engine path's `success` re-apply on a stale plan
(#5854) is a silent double-complete; the composition path already refuses, so the
two paths must behave the same.

**Independent Test**: With the commit forced stale, the engine path returns a
`blocked` decision and never calls `next_step`; a non-stale engine advance still
issues its next step (positive control).

**Acceptance Scenarios**:

1. **Given** a run whose persisted state changed after the advance was planned,
   **When** the engine path commits, **Then** it returns a `blocked` decision with
   a named reason and writes nothing — it does not re-plan through `next_step`.
2. **Given** a run that did not change, **When** the engine path commits, **Then**
   it issues the planned step (positive control).

---

### User Story 3 - A run cursor has at most one writer at a time (Priority: P2)

**Why this priority**: The lock is the structural closure of the defect class; it
also covers `provide_decision_answer`, the third unguarded writer, so a
`next --answer` cannot race an advance.

**Independent Test**: Two concurrent cursor mutations (advance/advance, or
answer/advance) are serialised: exactly one commits, the other observes the change
and refuses or reconciles; neither mutation is silently lost.

**Acceptance Scenarios**:

1. **Given** two cursor-mutating operations on one run, **When** they overlap,
   **Then** their read→modify→write is serialised and no mutation is lost.
2. **Given** an answer overlapping an advance, **When** both run, **Then** the
   answer survives and the advance is reconciled, not clobbered.

### Edge Cases

- A lock held longer than the stale-reclaim ceiling would let another process
  adopt it; the lock must be held only around the commit, never across
  step-contract executor dispatch, so this cannot arise in normal operation.
- Run start writes `state.json` into a private, not-yet-published run directory,
  so it needs no cursor lock — but its snapshot write must not collide on a shared
  temp name with any other writer.
- A lock cannot be acquired within the bounded wait (pathological contention): the
  advance must surface this as the same `blocked` refusal shape, not an uncaught
  exception.

## Requirements *(mandatory)*

Addresses #5854 and #5682.

### Functional Requirements

| ID | Title | User Story | Priority | Status | Delivery | No-op passable? |
|----|-------|------------|----------|--------|----------|-----------------|
| FR-001 | Record the caller-evaluated step and refuse a changed cursor | As an orchestrator, I want an advance that was planned against one issued step to refuse when the run's issued step changed before it commits, so that no caller completes a step it never evaluated (#5682, both composition and engine paths). | High | Open | [build] | no — paired with FR-002's positive-control scenario (a non-overlapping advance completes) on the same fixture |
| FR-002 | Engine path refuses a stale advance, never re-plans success | As an orchestrator, I want the engine advance path to return a `blocked` decision with a named reason on a stale plan and never re-apply `success` through `next_step`, so that it matches the composition path (#5854). | High | Open | [build] | no — same-fixture positive control: a non-stale engine advance still issues its next step |
| FR-003 | Serialise the run-cursor read-modify-write | As an orchestrator, I want at most one operation mutating a given run's cursor at a time, so that two overlapping advances cannot both append events and last-write-wins the snapshot. | High | Open | [build] | no — positive control: two concurrent advances yield exactly one completion and one refusal; a lone advance completes |
| FR-004 | Decision-answer participates in the same serialisation | As an operator, I want `provide_decision_answer` to serialise against a concurrent advance, so that a `next --answer` overlapping a `next` cannot silently lose either mutation. | Medium | Open | [build] | no — positive control: an answer with no overlap still records the answer and advances pending state |
| FR-005 | Refusal reuses the existing next contract | As a harness, I want a refused stale/contended advance to surface as the existing `kind=blocked` + `reason` with exit 1, so that no new decision kind or exit code is introduced. | High | Open | [ratchet] | no — positive control: a passing advance still yields `step`/`terminal` and exit 0 on the same contract |
| FR-006 | Snapshot writes use a unique staging file | As an orchestrator, I want each snapshot write staged under a unique temp name, so that two writers to the same run directory cannot collide on the staging path. | Medium | Open | [build] | no — positive control: a single writer still publishes a valid `state.json` |

### Non-Functional Requirements

| ID | Title | Requirement | Category | Priority | Status |
|----|-------|-------------|----------|----------|--------|
| NFR-001 | Bounded lock hold | The run-cursor lock is held only around the commit read-modify-write and never across step-contract executor dispatch; the hold stays well under the primitive's 10 s max-hold default so the 60 s stale-reclaim never triggers in normal operation. | Reliability | High | Open |
| NFR-002 | CLI latency preserved | An uncontended `next` adds at most one lock acquire/release to the advance; the typical-project CLI budget (< 2 s) is preserved. | Performance | Medium | Open |
| NFR-003 | Single canonical lock primitive | All cross-process locking routes through `kernel.locks.machine_file_lock`; no raw OS lock or third-party lock is introduced (the `test_lock_primitive_ban.py` gate stays green with an empty allowlist). | Portability | High | Open |

### Constraints

| ID | Title | Constraint | Category | Priority | Status |
|----|-------|------------|----------|----------|--------|
| C-001 | Runtime layer boundary | The fix lives in `src/runtime/next/` and `kernel`; the runtime layer must not import `specify_cli` (layer rules), so the `specify_cli` mission-write lock is not reusable here. | Technical | High | Open |
| C-002 | No deadlock with the run-index lock | The run-index lock taken during run start must be fully released before any run-cursor lock is acquired; the two are never held nested. | Technical | High | Open |
| C-003 | Minimise call-site churn pending a sibling branch | While `issue-5883-mission-writer-followups` is unmerged, keep lock/CAS logic in the engine/run-index layer and keep edits to `runtime_bridge*.py` call sites minimal to ease the later merge. | Technical | Medium | Open |
| C-004 | Scope boundary | Out of scope: #5112 (livelock), #5099, #5853, #5855. A refusing path may make a retry loop spin on `blocked`; this mission guarantees run-state integrity, not executor exactly-once or loop liveness. | Scope | High | Open |

### Key Entities

- **Run cursor**: the per-run `state.json` (`issued_step_id`, `completed_steps`,
  `pending_decisions`, …) plus the append-only `run.events.jsonl`; the resource
  this mission serialises.
- **Advance plan**: what one `next` will do, computed without writing; carries the
  snapshot it was planned from and (new) the caller-evaluated issued step.
- **Run-cursor lock**: a dedicated per-run-directory lock sidecar guarding the
  commit read-modify-write.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Two overlapping `next --result success` advances on one run never both complete a step — the later-committing one is refused with a named reason. — [build] · no-op passable: no (positive control: sequential advances both complete)
- **SC-002**: With a WP in `for_review` and the cursor at `implement`, an overlapping stale advance can no longer mark `review` done or reach `terminal` with the WP unreviewed (the #5682 field symptom). — [build] · no-op passable: no
- **SC-003**: Three deterministic, no-sleep red-first reproductions (the #5682 composition window, the #5854 engine double-apply, and the answer-vs-`next` race) are RED on the mission base and GREEN on the final commit; reverting any one independent part of the fix turns at least one of them red again. — [build] · no-op passable: no
- **SC-004**: A refused advance produces the same `next` output shape (`kind=blocked` + `reason`, exit 1) as today's composition-path blocked output — no new kind, no new exit code. — [ratchet] · no-op passable: no (positive control: a passing advance yields `step`/`terminal`, exit 0)
