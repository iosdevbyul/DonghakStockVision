from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from donghak_stock_vision.data.learning import FEATURES, AnalysisError, canonical, digest
from donghak_stock_vision.models.inference import ResearchPolicy, ResearchScore
from donghak_stock_vision.signals.domain import (
    SignalContext,
    SignalDirection,
    SignalPolicy,
    SignalStrength,
    build_signal,
)
from donghak_stock_vision.signals.research import generate_signal

VALUES = (0.0, 0.0, -0.1, 0.0, 0.0, 0.0, 0.0, 0.0)
UP, DOWN = "a" * 64, "b" * 64
POLICY = SignalPolicy("raw-v1", ResearchPolicy("quality-v1", frozenset()))


def context() -> SignalContext:
    return SignalContext(
        "005930",
        datetime(2025, 1, 2, tzinfo=UTC),
        datetime(2025, 1, 1, tzinfo=UTC),
        "dataset",
        "snapshot",
        "c" * 64,
        digest([list(FEATURES), list(VALUES)]),
        "prior_decline",
        (),
        "historical_research",
        "synthetic",
    )


def outputs() -> tuple[ResearchScore, ResearchScore]:
    return (
        ResearchScore("up", 0.5, "scored", UP, POLICY.research.fingerprint, ()),
        ResearchScore("down", None, "context_mismatch", DOWN, POLICY.research.fingerprint, ()),
    )


def test_raw_score_not_trade() -> None:
    up, down = outputs()
    result = build_signal(context(), POLICY, up_model=UP, down_model=DOWN, up=up, down=down)
    assert result.up.raw_score == 0.5
    assert result.up.strength == SignalStrength.UNCLASSIFIED
    assert result.down.raw_score is None
    assert result.down.strength == SignalStrength.NOT_APPLICABLE
    assert result.up.direction == SignalDirection.UP
    assert result.to_dict()["executable"] is False
    assert result.to_dict()["usage_restriction"] == "synthetic_test_only"
    assert "BUY" not in canonical(result.to_dict())
    assert result.fingerprint == replace(result).fingerprint
    with pytest.raises(FrozenInstanceError):
        result.up.raw_score = 0.9  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes",
    [
        {"score": float("nan")},
        {"score": float("inf")},
        {"score": -1.0},
        {"score": 1.1},
        {"score": True},
        {"score": None},
        {"status": "BUY"},
        {"direction": "down"},
        {"artifact_fingerprint": DOWN},
        {"policy_fingerprint": "c" * 64},
        {"quality_flags": ("other",)},
        {"executable": True},
        {"operational_eligible": True},
    ],
)
def test_bad_output(changes: dict[str, Any]) -> None:
    up, down = outputs()
    with pytest.raises(AnalysisError):
        build_signal(
            context(), POLICY, up_model=UP, down_model=DOWN, up=replace(up, **changes), down=down
        )


def test_conditional_conflict() -> None:
    up, down = outputs()
    with pytest.raises(AnalysisError, match="conditional_signal_conflict"):
        build_signal(
            context(),
            POLICY,
            up_model=UP,
            down_model=DOWN,
            up=up,
            down=replace(down, score=0.8, status="scored"),
        )
    with pytest.raises(AnalysisError, match="invalid_signal_null"):
        build_signal(
            context(), POLICY, up_model=UP, down_model=DOWN, up=up, down=replace(down, score=0.0)
        )


class Stub:
    def __init__(self, output: ResearchScore) -> None:
        self.output = output
        self.calls = 0

    def research(
        self,
        names: tuple[str, ...],
        values: tuple[float, ...],
        *,
        mode: str,
        quality_flags: frozenset[str],
        policy: ResearchPolicy,
    ) -> ResearchScore:
        self.calls += 1
        return self.output


def test_quality_gate_and_adapter() -> None:
    u, d = outputs()
    up, down = Stub(u), Stub(d)
    ctx = context()
    kwargs: dict[str, Any] = dict(names=FEATURES, up_model=UP, down_model=DOWN, up=up, down=down)
    first = generate_signal(ctx, POLICY, VALUES, **kwargs)
    assert first.fingerprint == generate_signal(ctx, POLICY, VALUES, **kwargs).fingerprint
    blocked = replace(ctx, quality_flags=("research_sessions_excluded_not_holidays",))
    before = up.calls
    result = generate_signal(blocked, POLICY, VALUES, **kwargs)
    assert up.calls == before
    assert result.up.status == result.down.status == "quality_blocked"
    assert result.to_dict()["quality_allowed"] is False
    permitted = SignalPolicy(
        "explicit-v1", ResearchPolicy("explicit", frozenset(blocked.quality_flags))
    )
    up.output = replace(
        u, policy_fingerprint=permitted.research.fingerprint, quality_flags=blocked.quality_flags
    )
    down.output = replace(
        d, policy_fingerprint=permitted.research.fingerprint, quality_flags=blocked.quality_flags
    )
    allowed = generate_signal(blocked, permitted, VALUES, **kwargs)
    assert allowed.up.raw_score == 0.5
    assert allowed.fingerprint != result.fingerprint


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "point_in_time"},
        {"mode": "operational"},
        {"population": "unknown"},
        {"dataset_id": ""},
        {"input_hash": "bad"},
        {"quality_flags": ["x"]},
        {"as_of": datetime(2025, 1, 1)},
        {"data_cutoff": datetime(2026, 1, 1, tzinfo=UTC)},
    ],
)
def test_bad_context(changes: dict[str, Any]) -> None:
    with pytest.raises(AnalysisError):
        replace(context(), **changes)


def test_identity_and_offsets() -> None:
    u, d = outputs()

    def build(ctx: SignalContext) -> str:
        return build_signal(ctx, POLICY, up_model=UP, down_model=DOWN, up=u, down=d).fingerprint

    from datetime import timezone

    ctx = context()
    assert build(ctx) == build(
        replace(ctx, as_of=ctx.as_of.astimezone(timezone(timedelta(hours=9))))
    )
    assert build(ctx) != build(replace(ctx, dataset_id="different"))
    assert build(ctx) != build(replace(ctx, snapshot_id="different"))


def test_feature_binding() -> None:
    up, down = (Stub(x) for x in outputs())
    for ctx, names in [
        (replace(context(), feature_hash="d" * 64), FEATURES),
        (context(), tuple(reversed(FEATURES))),
        (replace(context(), population="prior_rise"), FEATURES),
    ]:
        with pytest.raises(AnalysisError):
            generate_signal(
                ctx, POLICY, VALUES, names=names, up_model=UP, down_model=DOWN, up=up, down=down
            )
    assert up.calls == down.calls == 0


@pytest.mark.parametrize("population", ["prior_rise", "flat"])
def test_other_populations(population: str) -> None:
    u, d = outputs()
    u = replace(u, score=None, status="context_mismatch")
    if population == "prior_rise":
        d = replace(d, score=0.75, status="scored")
    candidate = build_signal(
        replace(context(), population=population),
        POLICY,
        up_model=UP,
        down_model=DOWN,
        up=u,
        down=d,
    )
    assert candidate.up.raw_score is None
    assert candidate.down.raw_score == (0.75 if population == "prior_rise" else None)


def test_missing_and_blocked_outputs() -> None:
    u, d = outputs()
    with pytest.raises(AnalysisError, match="missing_research_score"):
        build_signal(context(), POLICY, up_model=UP, down_model=DOWN, up=None, down=d)
    with pytest.raises(AnalysisError, match="scores_on_blocked_quality"):
        build_signal(
            replace(context(), quality_flags=("unapproved",)),
            POLICY,
            up_model=UP,
            down_model=DOWN,
            up=u,
            down=d,
        )
