"""Static approval checks and synthetic signal fixtures; no research evaluation."""

import ast
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from donghak_stock_vision.backtest.baseline_policy import (
    BaselineBacktestPolicy,
    CandidateAssessment,
    assess_candidate,
    baseline_rules,
)
from donghak_stock_vision.backtest.baseline_quality import (
    APPROVED_FLAGS,
    INVENTORY,
    BaselineQualityPolicy,
)
from donghak_stock_vision.backtest.validation import ContractError
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models.inference import ResearchScore
from donghak_stock_vision.signals.domain import SignalCandidate, build_signal
from tests.backtest.test_baseline_policy import COSTS, END, START, account, artifact, signal
from tests.backtest.test_baseline_runner import runner
from tests.backtest.test_research_contract import window
from tests.learning.test_signal_domain import DOWN, VALUES


def evaluate(candidate: SignalCandidate, *, policy: BaselineBacktestPolicy) -> CandidateAssessment:
    return assess_candidate(
        policy,
        policy.contract(START, END),
        candidate,
        up_artifact=artifact(),
        values=VALUES,
        window=window(),
        account=account(),
    )


QUALITY = BaselineQualityPolicy()
POLICY = BaselineBacktestPolicy(COSTS)


def flagged(flags: tuple[str, ...]) -> SignalCandidate:
    base = signal()
    policy = QUALITY.signal_policy()
    context = replace(base.context, quality_flags=flags)
    rejected = policy.rejected_flags(context)
    up = ResearchScore(
        "up", 0.5, "scored", artifact().fingerprint, policy.research.fingerprint, flags
    )
    down = ResearchScore("down", None, "context_mismatch", DOWN, policy.research.fingerprint, flags)
    return build_signal(
        context,
        policy,
        up_model=artifact().fingerprint,
        down_model=DOWN,
        up=None if rejected else up,
        down=None if rejected else down,
    )


def test_inventory_covers_existing_snapshot_flags_and_validation_guards() -> None:
    root = Path("src/donghak_stock_vision")
    inventory = dict(QUALITY.classifications)
    assert len(inventory) == len(INVENTORY)
    paths = (
        "data/snapshot.py",
        "features/engine.py",
        "signals/integration.py",
        "signals/domain.py",
        "models/inference.py",
        "models/artifact_resolver.py",
    )
    for path in paths:
        tree = ast.parse((root / path).read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "AnalysisError"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                assert getattr(node.args[0], "value", None) in inventory
            if path == "data/snapshot.py":
                # Snapshot flags are assigned/extended/appended, never inferred from data.
                if isinstance(node, (ast.Assign, ast.AugAssign)):
                    target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                    if isinstance(target, ast.Name) and target.id == "flags":
                        assert all(
                            n.value in inventory
                            for n in ast.walk(node.value)
                            if isinstance(n, ast.Constant) and isinstance(n.value, str)
                        )
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name) and node.func.value.id == "flags":
                        assert getattr(node.args[0], "value", None) in inventory


@pytest.mark.parametrize("flag", APPROVED_FLAGS)
def test_informational_flags_allow_candidate(flag: str) -> None:
    assert QUALITY.classify(flag) == "ALLOWED"
    assert evaluate(flagged((flag,)), policy=POLICY).candidate is not None


@pytest.mark.parametrize("state", [r[0] for r in INVENTORY if r[2] == "BLOCKED"])
def test_every_blocked_state_prevents_intent(state: str) -> None:
    assert evaluate(flagged((state,)), policy=POLICY).candidate is None
    assert QUALITY.classify(state) == "BLOCKED"


def test_directional_context_and_upstream_rejection_preserved() -> None:
    assert QUALITY.signal_reason(signal(population="prior_rise")) == "up_context_unavailable"
    assert QUALITY.signal_reason(flagged(("corporate_actions_unverified",))) == "quality_blocked"
    good = flagged(APPROVED_FLAGS)
    assert good.down.status == "context_mismatch" and good.down.raw_score is None
    assert QUALITY.signal_reason(good) is None


def test_unknown_is_explicit_error_not_silent_rejection() -> None:
    with pytest.raises(ContractError, match="unknown_quality_state:new_future_flag"):
        evaluate(flagged(("new_future_flag",)), policy=POLICY)
    with pytest.raises(ContractError, match="unknown_quality_state"):
        QUALITY.classify("new_enum_member")


def test_immutable_complete_and_no_defect_waiver() -> None:
    with pytest.raises(FrozenInstanceError):
        QUALITY.version = "changed"  # type: ignore[misc]
    with pytest.raises(ContractError, match="incomplete_quality_classification"):
        replace(QUALITY, classifications=QUALITY.classifications[:-1])
    with pytest.raises(ContractError, match="quality_failure_cannot_be_waived"):
        replace(
            QUALITY,
            classifications=tuple(
                (n, "ALLOWED" if n == "no_trade_bar" else a) for n, a in QUALITY.classifications
            ),
        )
    with pytest.raises(ContractError, match="quality_failure_cannot_be_waived"):
        replace(POLICY, allowed_research_flags=("corporate_actions_unverified",))


def test_identity_includes_classification_and_baseline_integration() -> None:
    strict = replace(
        QUALITY,
        classifications=tuple(
            (n, "BLOCKED" if n == "universe_incomplete" else a) for n, a in QUALITY.classifications
        ),
    )
    assert strict.fingerprint != QUALITY.fingerprint
    assert QUALITY.fingerprint == BaselineQualityPolicy().fingerprint
    assert POLICY.allowed_research_flags == APPROVED_FLAGS
    changed = replace(POLICY, quality_policy=strict, allowed_research_flags=strict.allowed_flags)
    assert changed.fingerprint != POLICY.fingerprint
    assert POLICY.to_dict()["quality_policy"] == QUALITY.to_dict()
    r = runner()
    upgraded = replace(
        r,
        policy=POLICY,
        contract=POLICY.contract(
            r.records[0].session.trading_date, r.records[-1].session.trading_date
        ),
        expected_policy_fingerprint=POLICY.fingerprint,
    )
    upgraded.validate()  # No simulation or scoring.
    with pytest.raises(ContractError, match="locked_policy_mismatch"):
        replace(
            upgraded,
            expected_policy_fingerprint="b0c0a6aebc95f8952dc3db7f2262bd854cf4896b52ba6c5e92207a9e740d2b37",
        ).validate()


def test_economic_rules_unchanged_from_merged_runner() -> None:
    # Frozen from c4045d5; independent of Git history availability in shallow CI.
    new = baseline_rules()
    assert new.pop("strategy_version") == "long_only_up_reversal_baseline_v3"
    assert digest(new) == "0e38cd0d1f21474c59e6785b2ad8acbfc1581f562e212db7fa31269a2a4b9e7c"


def test_signal_enums_and_states_are_explicit() -> None:
    from donghak_stock_vision.signals.domain import SignalStrength

    assert all(s.value in dict(QUALITY.classifications) for s in SignalStrength)
    tree = ast.parse(Path("src/donghak_stock_vision/signals/domain.py").read_text())
    expected = next(
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "expected"
        and isinstance(n.value, ast.Dict)
    )
    assert all(getattr(k, "value", None) in dict(QUALITY.classifications) for k in expected.keys)


@pytest.mark.parametrize(
    "flag", ["revision_history_unavailable", "corporate_actions_unverified", "future_unknown"]
)
def test_runner_quality_gate_before_candidate(flag: str) -> None:
    from dataclasses import dataclass
    from datetime import datetime

    from donghak_stock_vision.backtest.baseline_inputs import BaselineSession, BaselineSignalFrame
    from tests.backtest.test_baseline_runner import Signals, events

    @dataclass(frozen=True)
    class Source(Signals):
        def research(
            self, prefix: tuple[BaselineSession, ...], as_of: datetime
        ) -> BaselineSignalFrame:
            frame = super().research(prefix, as_of)
            ctx = replace(frame.signal.context, quality_flags=(flag,))
            rejected = self.policy.rejected_flags(ctx)
            up = ResearchScore(
                "up",
                0.5,
                "scored",
                self.up_artifact.fingerprint,
                self.policy.research.fingerprint,
                (flag,),
            )
            down = ResearchScore(
                "down", None, "context_mismatch", DOWN, self.policy.research.fingerprint, (flag,)
            )
            return replace(
                frame,
                signal=build_signal(
                    ctx,
                    self.policy,
                    up_model=self.up_artifact.fingerprint,
                    down_model=DOWN,
                    up=None if rejected else up,
                    down=None if rejected else down,
                ),
            )

    r = runner()
    r = replace(
        r,
        policy=POLICY,
        expected_policy_fingerprint=POLICY.fingerprint,
        contract=POLICY.contract(
            r.records[0].session.trading_date, r.records[-1].session.trading_date
        ),
        signals=Source(artifact(), QUALITY.signal_policy(), ()),
    )
    original = r.initial.identifier
    if flag == "future_unknown":
        with pytest.raises(ContractError, match="unknown_quality_state:future_unknown"):
            r.run()
    else:
        result = r.run()
        if flag == "corporate_actions_unverified":
            assert not events(result.document, "reservation")
            assert any(
                e["reason"] == "quality_blocked"
                for e in events(result.document, "signal_unavailable")
            )
        else:
            assert events(result.document, "reservation")
    assert r.initial.identifier == original


def test_published_quality_and_baseline_pins() -> None:
    assert QUALITY.fingerprint == "c091f3cd55eb0f2a93dafdede1a6e1ef8d59440cf23fc9ffaa25b495f03a489c"
    assert POLICY.fingerprint == "89a5c0f6de304b6a5b7033b97b925f752609f9245d2994b6cc1634aedb1e41a8"


def test_capture_flags_require_real_coverage_not_empty_approval() -> None:
    from donghak_stock_vision.backtest.baseline_inputs import PrefixMarketStore
    from donghak_stock_vision.data.snapshot import capture
    from tests.learning.helpers import CUTOFF, bars, quality

    source = bars(15, tickers=1)
    store = PrefixMarketStore(tuple(source))
    args = (
        store,
        [source[0].ticker],
        source[0].trading_date,
        source[-1].trading_date,
        CUTOFF,
    )
    complete = capture(
        *args, "historical_research", quality=quality(source), acknowledge=True, synthetic=True
    )
    assert complete["session_basis"] == "verified_sessions"
    assert not QUALITY.rejected_flags(tuple(complete["quality_flags"]))
    incomplete = capture(*args, "historical_research", acknowledge=True, synthetic=True)
    assert QUALITY.rejected_flags(tuple(incomplete["quality_flags"])) == (
        "calendar_unverified",
        "corporate_actions_unverified",
    )
