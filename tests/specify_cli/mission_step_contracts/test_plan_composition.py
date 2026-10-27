"""Every shipped planning action composes without a custom profile override.

The plan runtime has always declared these actions. A released wheel lacked
its contracts, and core's default-profile table still treated the built-in
plan type as custom even after the canonical pack supplied those contracts.
Exercise the real repository, graph and composer; only invocation persistence
is replaced with a recorder, so no model or external service is involved.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from charter.offering.missions.step_contracts import MissionStepContractRepository
from specify_cli.mission_step_contracts.executor import (
    StepContractExecutionContext,
    StepContractExecutionError,
    StepContractExecutor,
)
from tests._factories.activation_ledger import seed_activation_ledger

pytestmark = pytest.mark.fast

_EXPECTED = {
    "specify": "researcher-robbie",
    "research": "researcher-robbie",
    "plan": "architect-alphonso",
    "review": "reviewer-renata",
}


class _RecordingInvoker:
    def __init__(self) -> None:
        self.profiles: list[object] = []
        self.closed: list[str] = []

    def invoke(self, _request: str, **kwargs: object) -> object:
        self.profiles.append(kwargs["profile_hint"])
        return SimpleNamespace(invocation_id=f"plan-step-{len(self.profiles)}")

    def complete_invocation(self, invocation_id: str, *, outcome: str, closed_by: str) -> None:
        assert (outcome, closed_by) == ("done", "agent")
        self.closed.append(invocation_id)


@pytest.mark.parametrize("action,expected_profile", _EXPECTED.items())
def test_shipped_plan_action_composes_with_its_builtin_default(tmp_path: Path, action: str, expected_profile: str) -> None:
    contract = MissionStepContractRepository().get_by_action("plan", action)
    assert contract is not None, f"The wheel must ship plan/{action}"
    invoker = _RecordingInvoker()
    result = StepContractExecutor(repo_root=tmp_path, invocation_executor=invoker).execute(
        StepContractExecutionContext(repo_root=tmp_path, mission="plan", action=action, actor="pytest")
    )
    assert result.contract_id == f"plan-{action}"
    assert result.profile_hint == expected_profile
    assert [step.step_id for step in result.steps] == [step.id for step in contract.steps]
    assert invoker.profiles == [expected_profile] * len(contract.steps)
    assert list(result.invocation_ids) == invoker.closed


@pytest.mark.parametrize("action", _EXPECTED)
def test_plan_default_does_not_bypass_contract_denial(tmp_path: Path, action: str) -> None:
    (tmp_path / ".kittify").mkdir()
    (tmp_path / ".kittify" / "config.yaml").write_text("", encoding="utf-8")
    seed_activation_ledger(tmp_path, deactivated_mission_step_contracts=[f"plan/{action}"])
    invoker = _RecordingInvoker()
    with pytest.raises(StepContractExecutionError, match="not runnable"):
        StepContractExecutor(repo_root=tmp_path, invocation_executor=invoker).execute(
            StepContractExecutionContext(repo_root=tmp_path, mission="plan", action=action, actor="pytest")
        )
    assert invoker.profiles == []


def test_unknown_plan_action_still_refuses_before_invocation(tmp_path: Path) -> None:
    invoker = _RecordingInvoker()
    with pytest.raises(StepContractExecutionError, match="No step contract found"):
        StepContractExecutor(repo_root=tmp_path, invocation_executor=invoker).execute(
            StepContractExecutionContext(repo_root=tmp_path, mission="plan", action="unknown", actor="pytest")
        )
    assert invoker.profiles == []
