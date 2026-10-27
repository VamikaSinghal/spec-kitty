"""A populated built-in plan advances through its four real CLI actions."""

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from specify_cli import app
from tests.next.test_discovery_step_contract import _scaffold_mission

pytestmark = [pytest.mark.integration, pytest.mark.git_repo]


def test_populated_plan_advances_without_profile_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slug = "plan-composition-regression"
    root = _scaffold_mission(tmp_path, slug, mission_type="plan", filled_plan=True)
    mission_dir = root / "kitty-specs" / slug
    (mission_dir / "spec.md").write_text(
        "# Plan problem\n\n## Functional Requirements\n\n- **FR-001**: Choose a reversible integration order.\n",
        encoding="utf-8",
    )
    (mission_dir / "research.md").write_text("# Research\n\nSource-backed alternatives.\n", encoding="utf-8")
    subprocess.run(["git", "add", str(mission_dir.relative_to(root))], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "populated planning artifacts"], cwd=root, check=True)
    monkeypatch.chdir(root)

    observed = []
    for expected in ("specify", "research", "plan", "review"):
        result = CliRunner().invoke(app, ["next", "--agent", "codex", "--mission", slug, "--result", "success", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["kind"] == "step", payload
        assert payload["action"] == expected, payload
        assert payload["guard_failures"] == [], payload
        observed.append(payload["action"])
    assert observed == ["specify", "research", "plan", "review"]
