"""Service settings loaded from environment without leaking values in responses."""

import os
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_MARKER = "AGENTS.md"


def find_repository_root() -> Path:
    """Locate the repository root without assuming a fixed directory depth.

    ``TRACEAML_REPOSITORY_ROOT`` wins when set (the container image sets it).
    Otherwise the nearest ancestor holding the repository marker is used, and
    an installed, non-editable copy falls back to the working directory.
    """
    override = os.environ.get("TRACEAML_REPOSITORY_ROOT")
    if override:
        return Path(override).resolve()
    for parent in Path(__file__).resolve().parents:
        if (parent / REPOSITORY_MARKER).is_file():
            return parent
    return Path.cwd()


REPOSITORY_ROOT = find_repository_root()


class Settings(BaseSettings):
    """Runtime configuration for the API service."""

    model_config = SettingsConfigDict(
        env_prefix="TRACEAML_",
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_version: str = Field(default="0.1.0")
    falkordb_host: str | None = Field(default=None)
    falkordb_port: int = Field(default=6379)
    graph_name: str = Field(default="traceaml")
    graph_query_timeout_ms: int = Field(default=5_000, ge=100, le=30_000)
    graph_pool_size: int = Field(default=16, ge=1, le=128)
    score_artifact: str = Field(
        default="artifacts/runs/hi-small-v8/baselines/inference_scores.parquet"
    )
    baseline_config_path: str = Field(
        default="artifacts/runs/hi-small-v8/baselines/baseline_config.json"
    )
    baseline_metrics_path: str = Field(
        default="artifacts/runs/hi-small-v8/baselines/baseline_metrics.json"
    )
    graph_metrics_path: str = Field(
        default="artifacts/runs/hi-small-v8/graph-ablation/baseline_metrics.json"
    )
    performance_path: str = Field(default="docs/benchmarks/iteration-2-performance.json")
    score_model: str = Field(default="lgbm")
    case_db_path: str = Field(default="data/cases/cases.db")
    # How the LLM agent reaches the graph tools. ``mcp`` routes every tool call
    # through the MCP server boundary; ``direct`` calls the query functions.
    agent_tool_transport: Literal["mcp", "direct"] = Field(default="mcp")

    # Optional investigation report agent. ``LLM_*`` names match .env.example;
    # the TRACEAML_ prefixed forms are accepted too. The provider is off
    # unless explicitly set, and the key is held as a secret so it never
    # appears in reprs, logs, or responses.
    llm_provider: Literal["none", "gemini"] = Field(
        default="none",
        validation_alias=AliasChoices("LLM_PROVIDER", "TRACEAML_LLM_PROVIDER"),
    )
    llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "LLM_API_KEY", "TRACEAML_LLM_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"
        ),
    )
    llm_model: str = Field(
        default="gemini-flash-latest",
        validation_alias=AliasChoices("LLM_MODEL", "TRACEAML_LLM_MODEL"),
    )
    llm_timeout_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=120.0,
        validation_alias=AliasChoices("LLM_TIMEOUT_SECONDS", "TRACEAML_LLM_TIMEOUT_SECONDS"),
    )
    llm_max_tool_calls: int = Field(
        default=6,
        ge=1,
        le=10,
        validation_alias=AliasChoices("LLM_MAX_TOOL_CALLS", "TRACEAML_LLM_MAX_TOOL_CALLS"),
    )
    llm_max_rounds: int = Field(
        default=3,
        ge=1,
        le=5,
        validation_alias=AliasChoices("LLM_MAX_ROUNDS", "TRACEAML_LLM_MAX_ROUNDS"),
    )
    llm_max_output_tokens: int = Field(
        default=1500,
        ge=200,
        le=8000,
        validation_alias=AliasChoices(
            "LLM_MAX_OUTPUT_TOKENS", "TRACEAML_LLM_MAX_OUTPUT_TOKENS"
        ),
    )
    # Optional per-million-token prices (USD). Unset by default: no price is
    # assumed, so no cost is reported until the operator supplies the rates.
    llm_price_input_per_mtok: float | None = Field(
        default=None,
        ge=0.0,
        validation_alias=AliasChoices(
            "LLM_PRICE_INPUT_PER_MTOK", "TRACEAML_LLM_PRICE_INPUT_PER_MTOK"
        ),
    )
    llm_price_output_per_mtok: float | None = Field(
        default=None,
        ge=0.0,
        validation_alias=AliasChoices(
            "LLM_PRICE_OUTPUT_PER_MTOK", "TRACEAML_LLM_PRICE_OUTPUT_PER_MTOK"
        ),
    )

    @field_validator("llm_provider", mode="before")
    @classmethod
    def _blank_provider_is_none(cls, value: object) -> object:
        """An empty ``LLM_PROVIDER=`` line in .env means no provider."""
        if isinstance(value, str):
            cleaned = value.strip().lower()
            return cleaned or "none"
        return value

    @field_validator(
        "llm_api_key", "llm_price_input_per_mtok", "llm_price_output_per_mtok", mode="before"
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """Empty ``KEY=`` lines in .env are treated as unset."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def llm_configured(self) -> bool:
        """True when a provider and a non-empty key are both set."""
        return (
            self.llm_provider != "none"
            and self.llm_api_key is not None
            and bool(self.llm_api_key.get_secret_value().strip())
        )


def get_settings() -> Settings:
    """Return settings from the current environment."""
    return Settings()
