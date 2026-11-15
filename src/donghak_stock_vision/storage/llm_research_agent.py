"""Common proposal policy outside all model transports."""

import json
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.models.llm_provider import LLMProvider
from donghak_stock_vision.models.research_agent import (
    decode_gemma_proposal,
    validate_gemma_proposal,
)
from donghak_stock_vision.storage.analysis import AnalysisStore
from donghak_stock_vision.storage.autonomous_research import DB, clock
from donghak_stock_vision.storage.gemma_research import prompt_view, proposal_schema


class LLMResearchAgent:
    def __init__(
        self,
        root: Path,
        provider: LLMProvider,
        on_success: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.root = root
        self.provider = provider
        self.on_success = on_success
        self.audit: dict[str, Any] = {}
        self.unloaded = True

    def propose(self, view: dict[str, Any]) -> str:
        import shutil

        require(
            shutil.disk_usage(self.root).free >= 1024**3
            and sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file()) <= 512 * 1024**2,
            "llm_disk_resource_limit",
        )
        # The whitelist projection remains common for local and approved remote models.
        summary = prompt_view(view)
        prompt = (
            "Select one allowed unexecuted experiment. Return schema JSON only. Copy exact "
            "id, family, featureSet and value; seed=42. Keep prose below 100 characters. "
            "Use the provided internal history; improvements are hypotheses, not verified "
            "performance. Never emit code, tools, source data or change safety gates.\n"
            + json.dumps(summary, separators=(",", ":"))
        )
        require(len(prompt.encode()) <= 14000, "llm_prompt_bound")
        config = self.provider.config
        with closing(AnalysisStore(self.root / DB)) as store:
            request_id = store.put(
                "llm_request",
                dict(
                    at=clock(),
                    configuration_id=config.fingerprint,
                    provider_id=config.provider_id,
                    model_id=config.model_id,
                    revision=config.revision,
                    summary=summary,
                    prompt=prompt,
                ),
            )
        self.audit = dict(
            request_id=request_id,
            provider_id=config.provider_id,
            model_id=config.model_id,
            revision=config.revision,
            configuration_id=config.fingerprint,
            state="rejected",
            started_at=clock(),
        )
        began = time.monotonic()
        try:
            text = self.provider.generate(prompt, proposal_schema(view))
            require(len(text) <= 8192, "llm_proposal_size")
            self.audit["response_text"] = text
            proposal = decode_gemma_proposal(text)
            chosen = validate_gemma_proposal(proposal, view)
            self.audit.update(state="accepted", proposal=proposal, selected=chosen)
            return chosen
        except Exception as error:
            import re

            reason = str(error)
            reason = (
                reason
                if re.fullmatch(r"(?:llm|gemma)_[a-z0-9_]{1,90}", reason)
                else "llm_proposal_rejected"
            )
            self.audit.update(reason=reason, error_class=type(error).__name__)
            raise ValueError(reason) from None
        finally:
            self.unloaded = self.provider.unloaded
            self.audit.update(
                metadata=self.provider.metadata,
                unloaded=self.unloaded,
                finished_at=clock(),
                wall_seconds=time.monotonic() - began,
            )
            self.provider.close()
            with closing(AnalysisStore(self.root / DB)) as store:
                store.put("llm_proposal", self.audit)
            if self.audit["state"] == "accepted" and self.unloaded and self.on_success is not None:
                self.on_success(self.audit)
