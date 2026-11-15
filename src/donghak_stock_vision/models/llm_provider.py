"""Model-independent research execution contracts; no transport or credentials."""

from dataclasses import asdict, dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

from donghak_stock_vision.backtest.validation import require
from donghak_stock_vision.data.learning import digest


@dataclass(frozen=True)
class ModelConfig:
    key: str
    provider_id: str
    model_id: str
    revision: str
    display_name: str
    endpoint: str
    version: str = "operator_configured_v1"
    timeout: int = 240
    temperature: float = 0.2
    context_window: int = 4096
    max_output_tokens: int = 384
    thinking: bool = False
    thinking_supported: bool | None = None
    structured_output: bool | None = None
    json_schema: bool | None = None
    execution_policy: str = "cpu"
    estimated_memory_mb: int = 6144
    max_memory_mb: int = 7168
    cpu_threads: int = 1
    max_cpu_percent: int = 450
    credential_env: str | None = None
    remote_enabled: bool = False
    approved_transfer_scope: str | None = None

    def __post_init__(self) -> None:
        import math
        import re

        require(
            all(
                type(v) is str and 0 < len(v) <= 200
                for v in (self.key, self.model_id, self.revision, self.display_name, self.version)
            ),
            "llm_model_identity",
        )
        require(self.provider_id in ("ollama", "openai_compatible"), "llm_provider_unsupported")
        u = urlsplit(self.endpoint)
        require(
            not u.username and not u.password and not u.query and not u.fragment,
            "llm_endpoint_credentials_forbidden",
        )
        require(
            all(
                type(v) is int
                for v in (
                    self.timeout,
                    self.context_window,
                    self.max_output_tokens,
                    self.estimated_memory_mb,
                    self.max_memory_mb,
                    self.cpu_threads,
                    self.max_cpu_percent,
                )
            ),
            "llm_integer_settings_required",
        )
        require(
            type(self.temperature) in (int, float)
            and math.isfinite(self.temperature)
            and type(self.remote_enabled) is bool,
            "llm_settings_types",
        )
        require(
            1 <= self.timeout <= 600
            and 512 <= self.context_window <= 4096
            and 64 <= self.max_output_tokens <= 512
            and 0 <= self.temperature <= 1,
            "llm_generation_bounds",
        )
        require(
            self.cpu_threads == 1
            and 256 <= self.estimated_memory_mb <= self.max_memory_mb <= 8192
            and 100 <= self.max_cpu_percent <= 450,
            "llm_resource_bounds",
        )
        require(
            all(
                v is None or type(v) is bool
                for v in (self.thinking_supported, self.structured_output, self.json_schema)
            )
            and type(self.thinking) is bool,
            "llm_capability_type",
        )
        require(not self.thinking or self.thinking_supported is True, "llm_thinking_unsupported")
        if self.provider_id == "ollama":
            require(
                self.endpoint == "http://127.0.0.1:11434"
                and self.execution_policy == "cpu"
                and self.credential_env is None,
                "llm_local_endpoint_policy",
            )
            require(
                re.fullmatch(r"[0-9a-f]{64}", self.revision) is not None,
                "llm_local_digest_required",
            )
        else:
            require(
                u.scheme == "https" and bool(u.hostname) and self.execution_policy == "remote",
                "llm_remote_endpoint_policy",
            )
            require(
                self.credential_env is not None
                and re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", self.credential_env) is not None,
                "llm_credential_reference",
            )

    @property
    def fingerprint(self) -> str:
        return digest(asdict(self))

    def require_execution(self) -> None:
        require(
            self.structured_output is True and self.json_schema is True,
            "llm_required_capabilities_unconfirmed",
        )
        if self.provider_id == "openai_compatible":
            require(
                self.remote_enabled
                and self.approved_transfer_scope == "bounded_research_summary_v1",
                "llm_remote_transfer_not_approved",
            )

    def public(self) -> dict[str, Any]:
        return dict(
            key=self.key,
            providerId=self.provider_id,
            modelId=self.model_id,
            displayName=self.display_name,
            version=self.version,
            revision=self.revision,
            location="local" if self.provider_id == "ollama" else "remote",
            capabilities=dict(
                thinking=self.thinking_supported,
                structuredOutput=self.structured_output,
                jsonSchema=self.json_schema,
                source="server_configuration_requires_validation",
            ),
            generationSettings=dict(
                timeoutSeconds=self.timeout,
                temperature=self.temperature,
                contextWindow=self.context_window,
                maxOutputTokens=self.max_output_tokens,
                thinking=self.thinking,
            ),
            resourceRequirement=dict(
                executionPolicy=self.execution_policy,
                estimatedMemoryMiB=self.estimated_memory_mb,
                maxMemoryMiB=self.max_memory_mb,
                cpuThreads=self.cpu_threads,
                maxCpuPercent=self.max_cpu_percent,
            ),
            configurationId=self.fingerprint,
        )


class LLMProvider(Protocol):
    config: ModelConfig
    unloaded: bool
    metadata: dict[str, Any]

    def discover(self) -> dict[str, Any]: ...
    def generate(self, prompt: str, schema: dict[str, Any]) -> str: ...
    def close(self) -> None: ...
