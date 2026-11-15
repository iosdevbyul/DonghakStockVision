"""Opt-in LLM control plane; additive immutable audit separate from market storage."""

from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest
from donghak_stock_vision.models.directional import catalog
from donghak_stock_vision.models.llm_provider import ModelConfig
from donghak_stock_vision.storage.analysis import AnalysisStore, ReadOnlyAnalysisStore
from donghak_stock_vision.storage.autonomous_research import clock
from donghak_stock_vision.storage.gemma_research import Ollama
from donghak_stock_vision.storage.llm_providers import ProviderFactory, local_resource_ready
from donghak_stock_vision.storage.llm_research_agent import LLMResearchAgent
from donghak_stock_vision.storage.research_resources import lock

CONTROL_DB = "model-management.sqlite3"


def contract_probe() -> dict[str, Any]:
    c = catalog([1], "agent_masks_v1")[0]
    return dict(
        version="research_agent_view_v1",
        dataset_version="contract_probe_no_market_data",
        goal=c["goal"],
        candidates=[dict(experiment_id=digest(c), config=c)],
        history=[],
        leader=None,
        budget=1,
    )


class ModelManagement:
    def __init__(
        self,
        root: Path,
        configs: list[ModelConfig],
        default_key: str,
        factory: ProviderFactory | None = None,
    ) -> None:
        require(root.is_absolute() and root.resolve() == root, "ai_private_root")
        require(
            0 < len(configs) <= 32 and len({c.key for c in configs}) == len(configs),
            "ai_catalog_bound",
        )
        self.root = root
        self.configs = {c.key: c for c in configs}
        self.default = default_key
        self.factory = factory or ProviderFactory()
        require(default_key in self.configs, "ai_default_unknown")
        if root.exists() and any(root.iterdir()):
            require((root / CONTROL_DB).is_file(), "ai_control_root_already_used")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(root.stat().st_mode & 0o077 == 0, "ai_private_permissions")
        path = root / CONTROL_DB
        require(not path.is_symlink(), "ai_control_symlink")
        with closing(AnalysisStore(path)) as store:
            catalogs = store.all("ai_catalog")
            catalog = dict(
                models=[asdict(c) for c in sorted(configs, key=lambda c: c.key)],
                default=default_key,
            )
            for _, prior in catalogs:
                require(prior["default"] == default_key, "ai_default_change_requires_selection")
                for previous in prior["models"]:
                    require(
                        previous["key"] in self.configs
                        and asdict(self.configs[previous["key"]]) == previous,
                        "ai_existing_model_configuration_immutable",
                    )
            if not any(v == catalog for _, v in catalogs):
                store.put("ai_catalog", catalog)
        path.chmod(0o600)
        require(
            (root / "experiments.sqlite3").resolve() == root / "experiments.sqlite3",
            "ai_probe_database_symlink",
        )
        # Existing experiment owner, used only for contract probe audit.
        with closing(AnalysisStore(root / "experiments.sqlite3")):
            pass
        (root / "experiments.sqlite3").chmod(0o600)

    def config(self, key: str) -> ModelConfig:
        require(key in self.configs, "ai_model_not_registered")
        return self.configs[key]

    def events(self, kind: str) -> list[tuple[str, dict[str, Any]]]:
        with closing(ReadOnlyAnalysisStore(self.root / CONTROL_DB)) as store:
            require(
                store.connection.execute("SELECT COUNT(*) FROM analysis_artifacts").fetchone()[0]
                <= 10000,
                "ai_audit_bound",
            )
            return store.all(kind)

    def selection(self) -> dict[str, Any]:
        rows = sorted((v for _, v in self.events("ai_selection")), key=lambda v: v["sequence"])
        previous = "0" * 64
        for i, v in enumerate(rows, 1):
            require(v["sequence"] == i and v["previous"] == previous, "ai_selection_chain")
            previous = digest(v)
        return (
            rows[-1]
            if rows
            else dict(
                sequence=0,
                model_key=self.default,
                configuration_id=self.config(self.default).fingerprint,
                default=True,
            )
        )

    def validation(
        self, key: str, identifier: str | None = None
    ) -> tuple[str, dict[str, Any]] | None:
        rows = [
            (k, v)
            for k, v in self.events("ai_validation")
            if v["model_key"] == key
            and v["configuration_id"] == self.config(key).fingerprint
            and (identifier is None or k == identifier)
        ]
        return max(rows, key=lambda r: r[1]["at"]) if rows else None

    def busy(self) -> bool:
        try:
            with (
                lock(self.root, ".selection.lock"),
                lock(self.root.parent, ".autonomous-heavy-work.lock"),
            ):
                return False
        except ValueError:
            return True

    def active(self) -> dict[str, Any]:
        current = self.selection()
        applied = self.events("ai_applied")
        last = max((v for _, v in applied), key=lambda v: v["at"], default=None)
        return dict(
            defaultModelKey=self.default,
            selectedModelKey=current["model_key"],
            selectedModel=self.config(current["model_key"]).public(),
            selectionVersion=current["sequence"],
            selectionId=digest(current),
            effectiveFrom="next_managed_research_run",
            lastApplied=None
            if last is None
            else {k: last[k] for k in ("model_key", "configuration_id", "at", "selection_version")},
            researchRunning=self.busy(),
            role="research_hypothesis_llm_not_stock_prediction_model",
        )

    def models(self) -> list[dict[str, Any]]:
        installed: dict[str, Any] = {}
        reachable = False
        backend = Ollama(self.factory.transport)
        try:
            installed = {m["name"]: m for m in backend.request("/api/tags")["models"]}
            reachable = True
        except Exception:
            pass
        finally:
            backend.close()
        current = self.selection()
        busy = self.busy()
        result = []
        for c in self.configs.values():
            validated = self.validation(c.key)
            v = None if validated is None else validated[1]
            ready = bool(
                v and v["valid"] and datetime.fromisoformat(v["expires_at"]) > datetime.now(UTC)
            )
            if c.provider_id == "ollama":
                present = c.model_id in installed and installed[c.model_id]["digest"] == c.revision
                configured = True
                available = present and reachable
            else:
                present = None
                configured = bool(self.factory.credential(c.credential_env or ""))
                available = bool(ready and configured and c.remote_enabled)
            reasons = []
            if not available:
                reasons.append("model_unavailable_or_unverified")
            if not ready:
                reasons.append("validation_required")
            if not local_resource_ready(c):
                reasons.append("resources_not_ready")
            if busy:
                reasons.append("research_or_validation_running")
            result.append(
                c.public()
                | dict(
                    installed=present,
                    configured=configured,
                    available=available,
                    executable=not reasons,
                    reachable=reachable if c.provider_id == "ollama" else None,
                    selected=current["model_key"] == c.key,
                    validationStatus="valid"
                    if ready
                    else "not_validated"
                    if v is None
                    else "failed_or_expired",
                    lastValidationTime=None if v is None else v["at"],
                    canChange=not reasons,
                    changeBlockers=reasons,
                )
            )
        # Discovery never approves an unknown installed model or guesses capabilities.
        registered = {c.model_id for c in self.configs.values() if c.provider_id == "ollama"}
        for name, m in installed.items():
            if name not in registered:
                result.append(
                    dict(
                        key=None,
                        providerId="ollama",
                        modelId=name,
                        displayName=name,
                        revision=m["digest"],
                        location="local",
                        installed=True,
                        configured=False,
                        available=False,
                        capabilities=dict(thinking=None, structuredOutput=None, jsonSchema=None),
                        selected=False,
                        validationStatus="not_registered",
                        canChange=False,
                        changeBlockers=["server_registration_required"],
                    )
                )
        return result

    def validate(self, key: str, actor: str) -> dict[str, Any]:
        c = self.config(key)
        with (
            lock(self.root, ".selection.lock"),
            lock(self.root.parent, ".autonomous-heavy-work.lock"),
        ):
            agent = LLMResearchAgent(self.root, self.factory.create(c))
            valid = False
            reason = None
            try:
                agent.propose(contract_probe())
                valid = agent.unloaded
            except ValueError as error:
                reason = (
                    str(error)
                    if str(error).startswith(("llm_", "gemma_"))
                    else "provider_or_output_contract_validation_failed"
                )
            v = dict(
                model_key=key,
                configuration_id=c.fingerprint,
                at=clock(),
                expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                valid=valid,
                reason=reason,
                actor=actor,
                contract_probe=True,
                research_started=False,
                model_changed=False,
                unloaded=agent.unloaded,
                request_id=agent.audit.get("request_id"),
                validated_capabilities=dict(
                    jsonSchema=valid, structuredOutput=valid, thinking=c.thinking if valid else None
                ),
            )
            with closing(AnalysisStore(self.root / CONTROL_DB)) as store:
                identifier = store.put("ai_validation", v)
        return dict(
            validationId=identifier,
            modelKey=key,
            valid=valid,
            reason=reason,
            expiresAt=v["expires_at"],
            unloaded=agent.unloaded,
            researchStarted=False,
            modelChanged=False,
        )

    def _select(
        self, key: str, validation_id: str, expected: int, actor: str, approved: bool
    ) -> dict[str, Any]:
        c = self.config(key)
        with (
            lock(self.root, ".selection.lock"),
            lock(self.root.parent, ".autonomous-heavy-work.lock"),
        ):
            current = self.selection()
            validated = self.validation(key, validation_id)
            try:
                require(approved, "ai_explicit_approval_required")
                require(current["sequence"] == expected, "ai_selection_conflict")
                require(
                    validated is not None
                    and validated[1]["valid"]
                    and datetime.fromisoformat(validated[1]["expires_at"]) > datetime.now(UTC),
                    "ai_validation_required",
                )
                c.require_execution()
                require(local_resource_ready(c), "ai_resources_not_ready")
                adapter = self.factory.create(c)
                try:
                    discovered = adapter.discover()
                    require(
                        discovered["configured"]
                        and (c.provider_id != "ollama" or discovered["installed"]),
                        "ai_model_unavailable",
                    )
                finally:
                    adapter.close()
                value = dict(
                    sequence=expected + 1,
                    previous=digest(current) if expected else "0" * 64,
                    previous_model_key=current["model_key"],
                    model_key=key,
                    configuration_id=c.fingerprint,
                    validation_id=validation_id,
                    actor=actor,
                    at=clock(),
                    result="selected",
                    effective_from="next_managed_research_run",
                )
                with closing(AnalysisStore(self.root / CONTROL_DB)) as store:
                    store.put("ai_selection", value)
            except ValueError as error:
                with closing(AnalysisStore(self.root / CONTROL_DB)) as store:
                    store.put(
                        "ai_change_rejected",
                        dict(
                            at=clock(),
                            actor=actor,
                            model_key=key,
                            reason=str(error),
                            previous_model_key=current["model_key"],
                        ),
                    )
                raise
        return self.active()

    @contextmanager
    def lease(self) -> Iterator[ModelConfig]:
        with lock(self.root, ".selection.lock"):
            selected = self.selection()
            c = self.config(selected["model_key"])
            require(
                c.fingerprint == selected["configuration_id"], "ai_selected_configuration_changed"
            )
            v = self.validation(c.key)
            require(
                v is not None
                and v[1]["valid"]
                and datetime.fromisoformat(v[1]["expires_at"]) > datetime.now(UTC),
                "ai_validation_required",
            )
            c.require_execution()
            with closing(AnalysisStore(self.root / CONTROL_DB)) as store:
                store.put(
                    "ai_run_lease",
                    dict(
                        at=clock(),
                        model_key=c.key,
                        configuration_id=c.fingerprint,
                        selection_version=selected["sequence"],
                    ),
                )
            yield c

    def history(self) -> list[dict[str, Any]]:
        rows = [v for _, v in self.events("ai_selection")] + [
            v for _, v in self.events("ai_change_rejected")
        ]
        return [
            dict(
                at=v["at"],
                previousModelKey=v["previous_model_key"],
                previousProviderId=self.config(v["previous_model_key"]).provider_id,
                previousModelId=self.config(v["previous_model_key"]).model_id,
                modelKey=v["model_key"],
                providerId=self.config(v["model_key"]).provider_id,
                modelId=self.config(v["model_key"]).model_id,
                result=v.get("result", "rejected"),
                validationId=v.get("validation_id"),
                validationStatus="valid_at_selection" if v.get("validation_id") else "rejected",
                effectiveFrom=v.get("effective_from"),
                actor=v["actor"],
                reason=v.get("reason"),
            )
            for v in sorted(rows, key=lambda v: v["at"], reverse=True)[:200]
        ]

    def agent_for(self, root: Path, config: ModelConfig) -> LLMResearchAgent:
        require(root.parent == self.root.parent, "ai_shared_resource_parent_required")
        selected = self.selection()

        def success(audit: dict[str, Any]) -> None:
            with closing(AnalysisStore(self.root / CONTROL_DB)) as store:
                store.put(
                    "ai_applied",
                    dict(
                        at=clock(),
                        model_key=config.key,
                        configuration_id=config.fingerprint,
                        selection_version=selected["sequence"],
                        request_id=audit["request_id"],
                        result="verified_research_proposal",
                    ),
                )

        return LLMResearchAgent(root, self.factory.create(config), success)

    def record_run(
        self, root: Path, pin: str, config: ModelConfig, result: dict[str, Any], budget: int
    ) -> None:
        from donghak_stock_vision.storage.autonomous_research import DB, context, terminals

        plan, data = context(root, pin)
        selected = {v["experiment_id"] for v in result["selections_this_run"]}
        with closing(ReadOnlyAnalysisStore(root / DB)) as store:
            finished = terminals(store)
            requests = {
                v["request_id"] for v in result["selections_this_run"] if v.get("request_id")
            }
            proposals = [v for _, v in store.all("llm_proposal") if v["request_id"] in requests]
            metrics = [
                dict(
                    experimentId=k,
                    config=v["config"],
                    meanAP=v["mean_ap"],
                    stabilityAP=v["stability_ap"],
                    overfitGap=v["largest_train_gap"],
                )
                for k, v in finished.items()
                if k in selected and v["state"] == "completed"
            ]
        conditions = dict(
            dataset=plan["dataset_id"],
            source=plan["source_pin"],
            space=plan["configurations"],
            folds=data["folds"],
            budget=budget,
            limits=plan["limits"],
            implementation=plan["code"]["implementation_hash"],
            llmSafetyPolicy=dict(
                timeout=config.timeout,
                contextWindow=config.context_window,
                maxOutputTokens=config.max_output_tokens,
                temperature=config.temperature,
                thinking=config.thinking,
                maxMemoryMiB=config.max_memory_mb,
                cpuThreads=config.cpu_threads,
                maxCpuPercent=config.max_cpu_percent,
                transferScope=config.approved_transfer_scope,
            ),
        )
        value = dict(
            at=clock(),
            modelKey=config.key,
            providerId=config.provider_id,
            modelId=config.model_id,
            revision=config.revision,
            configurationId=config.fingerprint,
            comparisonContractId=digest(conditions),
            budget=budget,
            freshFits=result["trained_this_run"],
            cacheHits=result["cache_hits_this_run"],
            historyEntriesDelivered=sum(
                v.get("history_count", 0) for v in result["selections_this_run"]
            ),
            acceptedProposals=sum(v["state"] == "accepted" for v in result["selections_this_run"]),
            rejectedOrFallback=sum(v["state"] == "fallback" for v in result["selections_this_run"]),
            metrics=metrics,
            pauseReason=result["pause_reason"],
            proposals=[
                dict(
                    state=v["state"],
                    reason=v.get("reason"),
                    hypothesis=v.get("proposal", {}).get("hypothesis"),
                    parentExperimentId=v.get("proposal", {}).get("parentExperimentId"),
                    timingSeconds=v["wall_seconds"],
                    resources=v.get("metadata", {}),
                )
                for v in proposals
            ],
            performanceApproved=False,
            pitQualified=False,
            executionAuthorized=False,
        )
        with closing(AnalysisStore(self.root / CONTROL_DB)) as store:
            store.put("ai_research_run", value)

    def comparisons(self) -> list[dict[str, Any]]:
        return [v for _, v in self.events("ai_research_run")][-100:]

    def select(
        self, key: str, validation_id: str, expected: int, actor: str, approved: bool
    ) -> dict[str, Any]:
        try:
            return self._select(key, validation_id, expected, actor, approved)
        except ValueError as error:
            if str(error) == "autonomous_runner_already_active":
                with closing(AnalysisStore(self.root / CONTROL_DB)) as store:
                    store.put(
                        "ai_change_rejected",
                        dict(
                            at=clock(),
                            actor=actor,
                            model_key=key,
                            reason="research_running",
                            previous_model_key=self.selection()["model_key"],
                        ),
                    )
            raise

    def submit_validation(self, key: str, actor: str) -> str:
        self.config(key)
        with lock(self.root, ".validation-job.lock"):
            jobs = self.events("ai_validation_job")
            completed = {v["job_id"] for _, v in self.events("ai_validation_job_result")}
            # An explicit new request may retire a queued/interrupted job after
            # restart; no automatic provider replay occurs.
            with closing(AnalysisStore(self.root / CONTROL_DB)) as store:
                for prior_id, _ in jobs:
                    if prior_id not in completed:
                        store.put(
                            "ai_validation_job_result",
                            dict(
                                job_id=prior_id,
                                at=clock(),
                                state="interrupted",
                                reason="explicit_retry_after_restart",
                            ),
                        )
                return store.put(
                    "ai_validation_job",
                    dict(model_key=key, actor=actor, at=clock(), state="queued"),
                )

    def execute_validation_job(self, identifier: str) -> None:
        with lock(self.root, ".validation-job.lock"):
            if any(v["job_id"] == identifier for _, v in self.events("ai_validation_job_result")):
                return
            with closing(ReadOnlyAnalysisStore(self.root / CONTROL_DB)) as store:
                job = store.get("ai_validation_job", identifier)
            try:
                result = self.validate(job["model_key"], job["actor"])
                value = dict(job_id=identifier, at=clock(), state="completed", result=result)
            except Exception:
                value = dict(
                    job_id=identifier,
                    at=clock(),
                    state="failed",
                    reason="validation_failed_or_busy",
                )
            with closing(AnalysisStore(self.root / CONTROL_DB)) as store:
                store.put("ai_validation_job_result", value)

    def validation_job(self, identifier: str) -> dict[str, Any]:
        with closing(ReadOnlyAnalysisStore(self.root / CONTROL_DB)) as store:
            job = store.get("ai_validation_job", identifier)
        rows = [v for _, v in self.events("ai_validation_job_result") if v["job_id"] == identifier]
        if rows:
            return dict(
                jobId=identifier, modelKey=job["model_key"], **max(rows, key=lambda v: v["at"])
            )
        return dict(
            jobId=identifier,
            modelKey=job["model_key"],
            state="running_or_queued",
            startedAt=job["at"],
        )
