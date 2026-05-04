"""Centralised, validated configuration loaded from the environment."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration shared across services.

    Values are loaded from environment variables (12-factor) or, in local
    development, a ``.env`` file.  Secrets are wrapped in ``SecretStr`` so
    they are never accidentally logged.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Slack
    slack_bot_token: SecretStr = Field(default=SecretStr(""))
    slack_signing_secret: SecretStr = Field(default=SecretStr(""))

    # LLM
    groq_api_key: SecretStr = Field(default=SecretStr(""))
    llm_model: str = Field(default="groq:llama-3.3-70b-versatile")

    # Temporal
    temporal_host: str = Field(default="localhost:7233")
    temporal_namespace: str = Field(default="default")
    temporal_task_queue: str = Field(default="coding-task-queue")

    # GitHub
    github_token: SecretStr = Field(default=SecretStr(""))
    github_default_repo: str = Field(default="octocat/Hello-World")
    use_github_mock: bool = Field(default=True)

    # Conversation memory (DynamoDB)
    conversations_table: str = Field(default="")
    aws_region: str = Field(default="us-east-1")
    history_max_turns: int = Field(default=20)

    # Runtime
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    environment: str = Field(default="local")
    otel_exporter_otlp_endpoint: str = Field(default="")

    @property
    def slack_signature_verification_enabled(self) -> bool:
        return bool(self.slack_signing_secret.get_secret_value())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()
