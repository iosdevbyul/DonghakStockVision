"""Legacy bytes, private-owner isolation and hostile restore boundaries."""

from dataclasses import replace
from typing import Any

import pytest

from donghak_stock_vision.backtest.data import FrozenJSON
from donghak_stock_vision.backtest.mass_simulation import (
    BatchScheduler,
    Cancellation,
    FailureCode,
    RunBudget,
    SimulationFault,
    _CheckpointState,
    _PendingRecord,
)
from donghak_stock_vision.backtest.paper_simulation import PaperSimulationAdapter
from donghak_stock_vision.backtest.simulation_spec import ExperimentSpec, SimulationJob
from donghak_stock_vision.data.learning import digest
from tests.backtest.test_mass_simulation import SyntheticRunner, experiment, repin
from tests.backtest.test_paper_simulation import budget, scenario
from tests.backtest.test_paper_simulation import experiment as paper_experiment


@pytest.mark.parametrize(
    "mode,pin,size",
    [
        ("success", "dddcbd0dfb9ad19f152ff81e050931736f536b25d2128c6a631950945f8c13f9", 5002),
        ("failure", "818a7c9bb78cccf1abafd8afcd908545af2fe9a2f7b8132cfb20e1c036e2e602", 4969),
        ("cancel", "769140585cef1640ea4deba588a1a0437c7f94d14fcbb324de17964e289eafaa", 1571),
        ("interrupt", "a6695b77b3f332c20ddde888ec424b6b2179b785bf7dd47a7cc73d8e2bfca5c8", 1570),
    ],
)
def test_exact_preoptimization_v1_canonical_hashes(mode: str, pin: str, size: int) -> None:
    # Captured from main's original scheduler before implementation, not regenerated expectations.
    token = Cancellation()

    class Runner(SyntheticRunner):
        def run(
            self, exp: ExperimentSpec, job: SimulationJob, seed: int, work: RunBudget
        ) -> FrozenJSON:
            if job.key == "job-002":
                if mode == "failure":
                    raise RuntimeError("bounded-fixture")
                if mode == "cancel":
                    token.cancel()
                    work.check()
                if mode == "interrupt":
                    raise KeyboardInterrupt
            return super().run(exp, job, seed, work)

    owner = BatchScheduler(experiment(), clock=lambda: 0.0)
    owner.run(Runner(), token)
    checkpoint = owner.checkpoint()
    assert checkpoint.identifier == pin
    assert len(checkpoint.payload_json.encode()) == size
    restored = BatchScheduler(
        experiment(), checkpoint=FrozenJSON(checkpoint.payload_json), expected_checkpoint_hash=pin
    )
    assert restored.checkpoint() == checkpoint
    assert restored.results == owner.results


def test_paper_result_and_legacy_checkpoint_bytes_match_pr126() -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = paper_experiment(adapter, 10)
    owner = BatchScheduler(spec)
    results = owner.run(adapter)
    assert (
        owner.checkpoint().identifier
        == "00c3b8ce04ebf3ea2161b49fc847538e5108266015e1ade89ad0c13e367fc8ab"
    )
    assert (
        results[0].to_dict()["output"]["result_fingerprint"]
        == "bce26534de8aaf1d0d18173c64cdf41c710a4d58676b83e8a94ce938c2dd9e88"
    )


def test_commits_validate_only_new_records_and_export_legacy_bytes_on_demand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = BatchScheduler(experiment(100))
    documents: list[int] = []
    records: list[int] = []
    original_document = owner._document
    original_record = owner._validate_record

    def document(rows: list[dict[str, Any]]) -> FrozenJSON:
        documents.append(len(rows))
        return original_document(rows)

    def record(parent: _CheckpointState, value: FrozenJSON) -> _CheckpointState:
        records.append(parent.count)
        return original_record(parent, value)

    monkeypatch.setattr(owner, "_document", document)
    monkeypatch.setattr(owner, "_validate_record", record)
    results = owner.run(SyntheticRunner())
    assert len(results) == 100 and records == list(range(100))
    assert documents == []
    cp = owner.checkpoint()
    assert documents == [100]
    assert owner.checkpoint() is cp and documents == [100]
    assert owner.run(SyntheticRunner()) == results
    assert len(records) == 100  # Completed resume never validates/runs these private records anew.


def test_every_supplied_checkpoint_is_fully_validated_even_a_previously_verified_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = experiment()
    owner = BatchScheduler(spec)
    owner.run(SyntheticRunner())
    cp = owner.checkpoint()
    calls: list[int] = []
    original = BatchScheduler._validate_record

    def validate(
        self: BatchScheduler, parent: _CheckpointState, value: FrozenJSON
    ) -> _CheckpointState:
        calls.append(parent.count)
        return original(self, parent, value)

    monkeypatch.setattr(BatchScheduler, "_validate_record", validate)
    for document in (cp, cp, FrozenJSON(cp.payload_json)):
        restored = BatchScheduler(spec, checkpoint=document, expected_checkpoint_hash=cp.identifier)
        assert restored.checkpoint() == cp
    assert calls == list(range(10)) * 3


@pytest.mark.parametrize(
    "attack",
    [
        "changed_result",
        "reorder",
        "remove_middle",
        "remove_tail",
        "duplicate",
        "owner",
        "extra_cache",
    ],
)
def test_untrusted_json_cannot_borrow_incremental_validation(attack: str) -> None:
    owner = BatchScheduler(experiment())
    owner.run(SyntheticRunner())
    checkpoint = owner.checkpoint()
    document = checkpoint.to_dict()
    if attack == "changed_result":
        document["records"][0]["output"]["draw"] += 1
    elif attack == "reorder":
        document["records"].reverse()
    elif attack == "remove_middle":
        del document["records"][1]
    elif attack == "remove_tail":
        document["records"].pop()
    elif attack == "duplicate":
        document["records"].insert(1, dict(document["records"][0]))
    elif attack == "owner":
        document["experiment_fingerprint"] = "0" * 64
    elif attack == "extra_cache":
        document["incremental_state"] = {"validated": True, "output_bytes": 0}
    changed = repin(document)
    with pytest.raises(SimulationFault):
        BatchScheduler(
            experiment(), checkpoint=changed, expected_checkpoint_hash=checkpoint.identifier
        )
    # Re-pinning cannot repair structural corruption. A valid prefix/changed semantic
    # output is distinguishable only through the independently trusted original pin.
    if attack in {"reorder", "remove_middle", "duplicate", "owner", "extra_cache"}:
        with pytest.raises(SimulationFault):
            BatchScheduler(
                experiment(), checkpoint=changed, expected_checkpoint_hash=changed.identifier
            )


@pytest.mark.parametrize("field", ["strategy", "dataset", "seed", "experiment_id"])
def test_restore_cannot_cross_changed_experiment_references(field: str) -> None:
    spec = experiment()
    owner = BatchScheduler(spec)
    owner.run(SyntheticRunner())
    cp = owner.checkpoint()
    change: Any = {"seed": 43, "experiment_id": "another-experiment"}.get(field)
    if field in {"strategy", "dataset"}:
        original = getattr(spec, field).to_dict()
        change = FrozenJSON.freeze({**original, "content_hash": "0" * 64})
    changed = replace(spec, **{field: change})
    with pytest.raises(SimulationFault, match="checkpoint_mismatch"):
        BatchScheduler(changed, checkpoint=cp, expected_checkpoint_hash=cp.identifier)


def test_internal_pending_records_are_not_a_restore_format() -> None:
    owner = BatchScheduler(experiment())
    pending = _PendingRecord(owner._state, FrozenJSON.freeze({"validated": True}))
    with pytest.raises(SimulationFault, match="checkpoint_corruption"):
        BatchScheduler(experiment(), checkpoint=pending, expected_checkpoint_hash="0" * 64)  # type: ignore[arg-type]


def test_pending_append_rejects_another_owner_and_stale_parent() -> None:
    spec = experiment()
    producer = BatchScheduler(spec)
    producer.run(SyntheticRunner())
    first = FrozenJSON.freeze(producer.checkpoint().to_dict()["records"][0])
    owner = BatchScheduler(spec)
    pending = _PendingRecord(owner._state, first)
    other = BatchScheduler(spec)
    with pytest.raises(ValueError, match="stale_incremental_parent"):
        other._validate(pending)
    owner.run(SyntheticRunner())
    with pytest.raises(ValueError, match="stale_incremental_parent"):
        owner._validate(pending)


@pytest.mark.parametrize(
    "field,value",
    [
        ("count", 0),
        ("output_bytes", 0),
        ("record_bytes", 0),
        ("last_index", -1),
        ("attempt", 9),
        ("terminal", False),
        ("fingerprint", "0" * 64),
        ("owner", object()),
    ],
)
def test_invalid_internal_summary_is_not_a_permissive_budget_cache(field: str, value: Any) -> None:
    owner = BatchScheduler(experiment())
    owner.run(SyntheticRunner())
    owner._state = replace(owner._state, **{field: value})
    with pytest.raises(ValueError, match="incremental_"):
        owner.run(SyntheticRunner())
    with pytest.raises(ValueError, match="incremental_"):
        owner.checkpoint()


def test_failed_validation_transition_preserves_old_export_and_can_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = BatchScheduler(experiment(1))
    before = owner.checkpoint()
    original = owner._validate_record

    def interrupted(parent: _CheckpointState, record: FrozenJSON) -> _CheckpointState:
        validated = original(parent, record)
        assert validated.count == 1
        raise KeyboardInterrupt

    monkeypatch.setattr(owner, "_validate_record", interrupted)
    with pytest.raises(KeyboardInterrupt):
        owner.run(SyntheticRunner())
    assert owner.checkpoint() is before
    assert owner.results == ()
    monkeypatch.setattr(owner, "_validate_record", original)
    assert owner.run(SyntheticRunner())[0].to_dict()["status"] == "succeeded"


def test_public_result_owners_cannot_poison_private_records() -> None:
    owner = BatchScheduler(experiment())
    exposed = owner.run(SyntheticRunner())
    before = owner.checkpoint()
    modified = exposed[0].to_dict()
    modified["output"]["draw"] += 1
    object.__setattr__(exposed[0], "payload_json", FrozenJSON.freeze(modified).payload_json)
    assert owner.results[0] != exposed[0]
    assert owner.checkpoint() is before


def test_tampered_export_cache_fails_closed_and_is_not_an_input_validation_cache() -> None:
    owner = BatchScheduler(experiment())
    owner.run(SyntheticRunner())
    cached = owner.checkpoint()
    doc = cached.to_dict()
    doc["records"][0]["output"]["draw"] += 1
    altered = repin(doc)
    object.__setattr__(cached, "payload_json", altered.payload_json)
    with pytest.raises(ValueError, match="checkpoint_cache_corruption"):
        owner.checkpoint()
    # Restoring modified public evidence independently still checks every record and original pin.
    with pytest.raises(SimulationFault):
        BatchScheduler(experiment(), checkpoint=cached, expected_checkpoint_hash="0" * 64)


def test_summary_chain_detects_altered_ancestor_bytes_during_export() -> None:
    owner = BatchScheduler(experiment())
    owner.run(SyntheticRunner())
    ancestor = owner._state.parent
    assert ancestor is not None and ancestor.record is not None
    altered = ancestor.record.to_dict()
    altered["output"]["draw"] += 1
    object.__setattr__(ancestor.record, "payload_json", FrozenJSON.freeze(altered).payload_json)
    with pytest.raises(ValueError, match="incremental_record_corruption"):
        owner.checkpoint()


def test_semantically_forged_rehashed_output_still_requires_explicit_replay() -> None:
    adapter = PaperSimulationAdapter((scenario(),))
    spec = paper_experiment(adapter, 1)
    owner = BatchScheduler(spec)
    original = owner.run(adapter)[0].to_dict()["output"]
    forged = dict(original)
    execution = dict(forged["execution"])
    execution["counts"] = {**execution["counts"], "fills": 999}
    forged["execution"] = execution
    forged["execution_fingerprint"] = digest(execution)
    forged["result_fingerprint"] = digest(
        {k: v for k, v in forged.items() if k != "result_fingerprint"}
    )
    with pytest.raises(ValueError, match="paper_result_replay_mismatch"):
        adapter.replay_validate(spec, spec.jobs[0], FrozenJSON.freeze(forged), budget(spec))
    checkpoint = owner.checkpoint()
    restored = BatchScheduler(
        spec, checkpoint=checkpoint, expected_checkpoint_hash=checkpoint.identifier
    )
    assert restored.run(adapter)[0].to_dict()["output"] == original


def test_stale_validation_result_cannot_commit_over_a_new_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = BatchScheduler(experiment(1))
    original = owner._validate

    def changed_head(pending: FrozenJSON | _PendingRecord) -> _CheckpointState:
        validated = original(pending)
        owner._state = owner._root()  # Simulated stale/replaced owner during transition.
        return validated

    monkeypatch.setattr(owner, "_validate", changed_head)
    with pytest.raises(ValueError, match="stale_incremental_commit"):
        owner.run(SyntheticRunner())
    assert owner.results == ()
    assert owner.checkpoint().to_dict()["records"] == []


def test_legacy_maximum_escaped_metadata_fits_existing_budgets() -> None:
    spec = experiment(1)
    spec = replace(
        spec,
        jobs=(SimulationJob("\x01" * 127 + "k", FrozenJSON.freeze({})),),
        limits=replace(spec.limits, max_result_bytes=2, max_total_result_bytes=2),
    )

    class Failure(SyntheticRunner):
        def run(
            self, exp: ExperimentSpec, job: SimulationJob, seed: int, work: RunBudget
        ) -> FrozenJSON:
            raise SimulationFault(FailureCode.MISSING_INPUT, "\x01" * 255 + "d")

    owner = BatchScheduler(spec)
    result = owner.run(Failure())[0].to_dict()
    assert result["status"] == "failed" and result["failure"]["code"] == "missing_input"
    assert len(owner.checkpoint().payload_json.encode()) == 2868
    assert (
        owner.checkpoint().identifier
        == "c7247098b315d983430c708e1d2db3b829aee077443a3e59d4ba0db1d94e1a03"
    )
