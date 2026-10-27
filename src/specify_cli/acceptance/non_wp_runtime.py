"""Accept artifact-producing workflows through their existing frozen runtime.

The ordinary WP acceptance path is unchanged. This branch is only selected
when the recorded runtime has no WP-iteration step and no WP task files.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from runtime.next.runtime_bridge import read_runtime_acceptance_facts
from runtime.next.runtime_bridge_cores import MISSING_ARTIFACT_MESSAGE, evaluate_guards_strict
from runtime.next.runtime_bridge_io import gather_artifact_presence
from specify_cli.mission import MissionError, get_mission_for_feature
from specify_cli.mission_metadata import resolve_mission_identity
from specify_cli.runtime.resolver import required_artifacts_for
from specify_cli.task_utils import LANES

from .gates_core import PLANNING_ARTIFACT_ONLY_SKIP_REASON
from .summary_core import _build_recommended_fix_order, build_warnings, evaluate_path_conventions

if TYPE_CHECKING:
    from mission_runtime import OwnedCheckout
    from . import AcceptanceSummary


def collect_non_wp_runtime_summary(
    *,
    repo_root: Path,
    feature_dir: Path,
    status_feature_dir: Path,
    branch: str | None,
    worktree_root: Path,
    primary_repo_root: Path,
    git_dirty: list[str],
    strict_metadata: bool,
    owned: OwnedCheckout | None,
) -> AcceptanceSummary | None:
    from . import AcceptanceError, AcceptanceSummary, _check_needs_clarification, _optional_artifact_tokens, _planning_read_dir, _wp_tasks_read_dir

    identity = resolve_mission_identity(feature_dir)
    # This bounded repair admits only the built-in plan family. Preserve the
    # exact existing WP/custom path, including its runtime-independent gates.
    if identity.mission_type != "plan":
        return None
    runtime_root = owned.owned_root if owned is not None else repo_root
    try:
        facts = read_runtime_acceptance_facts(runtime_root, feature_dir.name)
    except (OSError, ValueError, RuntimeError) as exc:
        raise AcceptanceError("Cannot read canonical mission runtime for acceptance; no state was changed") from exc
    if facts is None or facts.wp_driven:
        return None
    wp_homes = {feature_dir, status_feature_dir, _wp_tasks_read_dir(repo_root, feature_dir.name, owned=owned)}
    if any(next((home / "tasks").glob("WP*.md"), None) is not None for home in wp_homes):
        return None
    if facts.mission_family != identity.mission_type:
        raise AcceptanceError("Mission type does not match its recorded runtime; acceptance refused")

    activity: list[str] = []
    if "review" not in facts.step_ids:
        activity.append("Plan runtime has no review action; acceptance refused")
    incomplete = sorted(set(facts.step_ids) - set(facts.completed_step_ids))
    if not facts.step_ids or incomplete or facts.issued_step_id or facts.blocked_reason or facts.pending_decision_ids:
        activity.append("Non-WP runtime is not complete; remaining steps: " + (", ".join(incomplete) or "pending runtime decision/result"))
    planning_dir = _planning_read_dir(repo_root, feature_dir.name, owned=owned)
    required: set[str] = set()
    missing: set[str] = set()
    missing_prefix = MISSING_ARTIFACT_MESSAGE.format(name="")
    for step_id in facts.step_ids:
        required.update(required_artifacts_for(step_id, facts.mission_family, repo_root=runtime_root))
        snapshot = gather_artifact_presence(status_feature_dir, mission_family=facts.mission_family, step_id=step_id, repo_root=repo_root, owned=owned)
        for failure in evaluate_guards_strict(snapshot):
            if failure.startswith(missing_prefix):
                missing.add(failure[len(missing_prefix) :])
            else:
                activity.append(f"{step_id}: {failure}")
    if not required:
        activity.append("Non-WP runtime has no declared blocking artifacts; acceptance refused")
    try:
        mission = get_mission_for_feature(feature_dir)
    except MissionError:
        mission = None
    optional_missing = [token for token in _optional_artifact_tokens(mission) if not (planning_dir / token).exists()]
    path_violations, path_warning, dedup = evaluate_path_conventions(mission, repo_root, feature_dir, planning_dir, strict_metadata=strict_metadata)
    optional_missing = [token for token in optional_missing if token not in dedup]
    warnings = build_warnings(missing_optional=optional_missing, path_violations=path_violations, path_convention_warning=path_warning)
    return AcceptanceSummary(
        feature=feature_dir.name,
        repo_root=repo_root,
        feature_dir=feature_dir,
        tasks_dir=feature_dir / "tasks",
        branch=branch,
        worktree_root=worktree_root,
        primary_repo_root=primary_repo_root,
        lanes={lane: [] for lane in LANES},
        work_packages=[],
        metadata_issues=[],
        activity_issues=activity,
        unchecked_tasks=[],
        needs_clarification=_check_needs_clarification([planning_dir / name for name in sorted(required)]),
        missing_artifacts=sorted(missing),
        optional_missing=optional_missing,
        git_dirty=git_dirty,
        path_violations=path_violations,
        warnings=warnings,
        acceptance_matrix_gate_skip_reason=PLANNING_ARTIFACT_ONLY_SKIP_REASON,
        recommended_fix_order=_build_recommended_fix_order(
            lanes={lane: [] for lane in LANES},
            metadata_issues=[],
            activity_issues=activity,
            unchecked_tasks=[],
            needs_clarification=[],
            missing_artifacts=sorted(missing),
            git_dirty=git_dirty,
            path_violations=path_violations,
            blocked_checks=[],
        ),
    )
