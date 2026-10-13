"""Runtime configuration. Every value can be set with an environment variable
prefixed with POM_ (for example POM_ANTHROPIC_API_KEY) or in a .env file."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from fastapi import Request

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="POM_", extra="ignore")

    # --- service
    env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite+aiosqlite:///./pom.db"
    cors_origins: list[str] = Field(default_factory=list)
    trusted_hosts: list[str] = Field(default_factory=lambda: ["*"])

    # --- auth
    admin_api_key: SecretStr | None = None
    # Optional token created at startup if it does not exist (handy for a single-user install).
    bootstrap_device_token: SecretStr | None = None
    # Let the desktop app save, test and delete the AI and voice keys (Settings > API keys).
    # Handy for one person running their own server; leave off for a shared company server.
    allow_device_key_management: bool = False
    # Encrypts keys saved from the app. If unset, a random secret is created in secret_file.
    key_encryption_secret: SecretStr | None = None
    secret_file: str = "./.pom_secret"

    # --- language model
    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    # Backup model used when the main one fails before replying (outage, overload, no credit).
    llm_fallback_provider: Literal["anthropic", "openai"] | None = None
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_base_url: str = "https://api.anthropic.com"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    max_output_tokens: int = 700
    temperature: float = 0.7
    history_turns: int = 12
    assistant_name: str = "Bea"
    organization_name: str = "Better Analytics"
    extra_instructions: str = ""

    # --- agent (goals, tools, research, browser, connected apps). Runs on Claude.
    agent_enabled: bool = True
    agent_model: str = "claude-opus-5-5"
    agent_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    agent_max_steps: int = 40  # model turns per run, a safety cap
    agent_max_concurrent_runs: int = 3  # per device
    approval_timeout_s: int = 1800  # an unanswered approval is denied after this
    scheduler_interval_s: int = 30
    data_dir: str = "./data"  # browser profiles and other files
    browser_headless: bool = True
    browser_allow_private_networks: bool = False  # let Bea's browser open localhost / intranet addresses
    # Microsoft 365: an Azure app registration (public client, "Allow public client flows" on).
    microsoft_client_id: str = ""
    microsoft_tenant: str = "common"
    # Google: an OAuth client of type "Desktop app" (or "Web" with the redirect below registered).
    google_client_id: str = ""
    google_client_secret: SecretStr | None = None
    # Where this server is reachable from the person's browser, for OAuth redirects.
    public_base_url: str = "http://127.0.0.1:8080"

    # --- Sarvam AI voice
    sarvam_api_key: SecretStr | None = None
    sarvam_base_url: str = "https://api.sarvam.ai"
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_tts_speaker: str = "kavya"
    sarvam_tts_pace: float = 1.05
    sarvam_tts_sample_rate: int = 24000
    sarvam_tts_codec: Literal["mp3", "wav"] = "mp3"
    sarvam_stt_model: str = "saaras:v4"
    default_language: str = "en-IN"

    # --- limits
    rate_limit_per_minute: int = 40
    rate_limit_burst: int = 15
    upstream_timeout_s: float = 60.0
    upstream_retries: int = 2
    max_message_chars: int = 4000
    max_tts_chars: int = 2500
    max_audio_bytes: int = 10 * 1024 * 1024

    def provider_configured(self, provider: str | None) -> bool:
        key = {"anthropic": self.anthropic_api_key, "openai": self.openai_api_key}.get(provider or "")
        return bool(key and key.get_secret_value())

    @property
    def llm_configured(self) -> bool:
        return self.provider_configured(self.llm_provider) or self.provider_configured(self.llm_fallback_provider)

    @property
    def voice_configured(self) -> bool:
        return bool(self.sarvam_api_key and self.sarvam_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()


def app_settings(request: Request) -> Settings:
    """Settings the running app was created with (use as a FastAPI dependency)."""
    return request.app.state.settings
