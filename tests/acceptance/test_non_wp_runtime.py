"""Non-WP acceptance is vouched for by a completed real runtime, not empty lanes."""

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from runtime.next._internal_runtime import next_step
from runtime.next.runtime_bridge import get_or_start_run
from specify_cli import app
from specify_cli.acceptance import collect_feature_summary
from tests.next.test_discovery_step_contract import _scaffold_mission

pytestmark = [pytest.mark.integration, pytest.mark.git_repo]


def _commit(root: Path) -> None:
    paths = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts]
    subprocess.run(["git", "add", "--", *paths], cwd=root, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-qm", "acceptance fixture"], cwd=root, check=True)


def _plan(tmp_path: Path, *, complete: bool = True) -> tuple[Path, Path, str]:
    slug = "non-wp-plan"
    root = _scaffold_mission(tmp_path, slug, mission_type="plan", filled_plan=True)
    (root / "docs").mkdir()
    (root / "docs" / "decision.md").write_text("# Decision workspace\n", encoding="utf-8")
    mission_dir = root / "kitty-specs" / slug
    (mission_dir / "spec.md").write_text("# Scope\n\n- **FR-001**: Decide an integration order.\n", encoding="utf-8")
    (mission_dir / "research.md").write_text("# Research\n\nCompared verified alternatives.\n", encoding="utf-8")
    _commit(root)
    run = get_or_start_run(slug, root, "plan")
    for _ in range(5 if complete else 4):
        next_step(run, result="success", agent_id="fixture")
    _commit(root)
    return root, mission_dir, slug


def test_completed_plan_accepts_without_fabricated_wp_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, mission_dir, slug = _plan(tmp_path)
    assert not (mission_dir / "tasks.md").exists()
    assert not (mission_dir / "lanes.json").exists()
    summary = collect_feature_summary(root, slug, mutate_matrix=False)
    assert summary.ok, summary.to_dict()
    assert summary.work_packages == []
    monkeypatch.chdir(root)
    result = CliRunner().invoke(app, ["accept", "--mission", slug, "--mode", "pr", "--actor", "fixture", "--no-commit", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["summary"]["ok"] is True


def test_unfinished_review_cannot_accept_an_empty_wp_set(tmp_path: Path) -> None:
    root, mission_dir, slug = _plan(tmp_path, complete=False)
    spec = mission_dir / "spec.md"
    spec.write_text(
        spec.read_text(encoding="utf-8") + "\n- **FR-002**: [NEEDS CLARIFICATION: which order?] <!-- decision_id: 01KNONWPDECISION0000000001 -->\n",
        encoding="utf-8",
    )
    _commit(root)
    summary = collect_feature_summary(root, slug, mutate_matrix=False)
    assert not summary.ok
    assert any("runtime" in issue.lower() and "review" in issue for issue in summary.activity_issues)
    # The non-WP path shares the WP path's summary tail: the open marker is fix-ordered.
    assert summary.needs_clarification == [str(mission_dir / "spec.md")]
    assert "Resolve open NEEDS CLARIFICATION markers." in summary.recommended_fix_order


@pytest.mark.parametrize("name", ["spec.md", "research.md", "plan.md"])
def test_completed_plan_rechecks_its_actual_artifacts(tmp_path: Path, name: str) -> None:
    root, mission_dir, slug = _plan(tmp_path)
    (mission_dir / name).unlink()
    _commit(root)
    summary = collect_feature_summary(root, slug, mutate_matrix=False)
    assert not summary.ok
    assert name in summary.missing_artifacts


def test_completed_plan_does_not_relax_clean_tree_gate(tmp_path: Path) -> None:
    root, _, slug = _plan(tmp_path)
    (root / "README.md").write_text("Unrelated dirty source\n", encoding="utf-8")
    summary = collect_feature_summary(root, slug, mutate_matrix=False)
    assert not summary.ok
    assert summary.git_dirty


def test_completed_plan_preserves_path_convention_gate(tmp_path: Path) -> None:
    root, _, slug = _plan(tmp_path)
    (root / "docs" / "decision.md").unlink()
    (root / "docs").rmdir()
    _commit(root)
    summary = collect_feature_summary(root, slug, mutate_matrix=False)
    assert not summary.ok
    assert summary.path_violations


def test_frozen_template_tampering_refuses_acceptance(tmp_path: Path) -> None:
    from specify_cli.acceptance import AcceptanceError

    root, _, slug = _plan(tmp_path)
    frozen = next((root / ".kittify" / "runtime" / "runs").glob("*/mission_template_frozen.yaml"))
    frozen.write_text(frozen.read_text(encoding="utf-8") + "\n# changed after runtime completion\n", encoding="utf-8")
    _commit(root)
    with pytest.raises(AcceptanceError, match="Cannot read canonical mission runtime"):
        collect_feature_summary(root, slug, mutate_matrix=False)


@pytest.mark.parametrize("family", ["software-dev", "unsupported-custom"])
def test_other_families_do_not_gain_empty_wp_acceptance(tmp_path: Path, family: str) -> None:
    from specify_cli.acceptance import AcceptanceError

    root = _scaffold_mission(tmp_path, "not-a-plan", mission_type=family, filled_plan=True)
    _commit(root)
    with pytest.raises(AcceptanceError, match="no tasks directory"):
        collect_feature_summary(root, "not-a-plan", mutate_matrix=False)


def test_plan_without_indexed_runtime_cannot_accept(tmp_path: Path) -> None:
    from specify_cli.acceptance import AcceptanceError

    root, _, slug = _plan(tmp_path)
    (root / ".kittify" / "runtime" / "feature-runs.json").unlink()
    _commit(root)
    with pytest.raises(AcceptanceError, match="no tasks directory"):
        collect_feature_summary(root, slug, mutate_matrix=False)


def test_retyped_metadata_cannot_borrow_plan_runtime_acceptance(tmp_path: Path) -> None:
    from specify_cli.acceptance import AcceptanceError

    root, mission_dir, slug = _plan(tmp_path)
    metadata = json.loads((mission_dir / "meta.json").read_text(encoding="utf-8"))
    metadata["mission_type"] = "software-dev"
    (mission_dir / "meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    _commit(root)
    with pytest.raises(AcceptanceError, match="no tasks directory"):
        collect_feature_summary(root, slug, mutate_matrix=False)


def test_wp_family_does_not_read_or_depend_on_plan_runtime_facts(tmp_path: Path) -> None:
    from specify_cli.acceptance import AcceptanceError

    root = _scaffold_mission(tmp_path, "wp-control", mission_type="software-dev", filled_plan=True)
    runtime = root / ".kittify" / "runtime"
    runtime.mkdir()
    (runtime / "feature-runs.json").write_text("not valid json", encoding="utf-8")
    _commit(root)
    with pytest.raises(AcceptanceError, match="no tasks directory"):
        collect_feature_summary(root, "wp-control", mutate_matrix=False)


def test_mismatched_runtime_mission_binding_refuses_acceptance(tmp_path: Path) -> None:
    from specify_cli.acceptance import AcceptanceError

    root, _, slug = _plan(tmp_path)
    state_file = next((root / ".kittify" / "runtime" / "runs").glob("*/state.json"))
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["mission_slug"] = "another-plan"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    _commit(root)
    with pytest.raises(AcceptanceError, match="Cannot read canonical mission runtime"):
        collect_feature_summary(root, slug, mutate_matrix=False)


def _non_wp_summary(root: Path, mission_dir: Path, *, status_dir: Path | None = None):
    from specify_cli.acceptance.non_wp_runtime import collect_non_wp_runtime_facts

    return collect_non_wp_runtime_facts(
        repo_root=root,
        feature_dir=mission_dir,
        status_feature_dir=status_dir or mission_dir,
        wp_tasks_dir=mission_dir,
        owned=None,
    )


@pytest.mark.parametrize("home", ["planning", "status"])
def test_wp_tasks_at_either_authoritative_home_keep_wp_gates(tmp_path: Path, home: str) -> None:
    root, mission_dir, _ = _plan(tmp_path)
    status_dir = root / "status-surface"
    status_dir.mkdir()
    tasks = (mission_dir if home == "planning" else status_dir) / "tasks"
    tasks.mkdir(exist_ok=True)
    (tasks / "WP01-control.md").write_text("# Work package control\n", encoding="utf-8")
    assert _non_wp_summary(root, mission_dir, status_dir=status_dir) is None


def test_missing_identity_cannot_select_non_wp_acceptance(tmp_path: Path) -> None:
    root, mission_dir, _ = _plan(tmp_path)
    (mission_dir / "meta.json").unlink()
    assert _non_wp_summary(root, mission_dir) is None


def test_malformed_identity_refuses_non_wp_acceptance(tmp_path: Path) -> None:
    from specify_cli.core.paths import MissionMetaReadError

    root, mission_dir, _ = _plan(tmp_path)
    (mission_dir / "meta.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(MissionMetaReadError):
        _non_wp_summary(root, mission_dir)
