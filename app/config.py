"""Application settings, loaded from environment variables / `.env`."""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Mapping

from pydantic import Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class SecurityAwareEnvSource(EnvSettingsSource):
    """Env source that preserves empty security flags so they fail closed.

    When `env_ignore_empty=True` is enabled on the model, empty environment variables
    are otherwise stripped and fall back to `.env` or defaults. For security flags
    (`public_demo` and `admin_token`), empty values in the real environment must NOT
    be ignored, ensuring they fail closed.
    """

    def _load_env_vars(self) -> Mapping[str, str | None]:
        vars_dict = dict(super()._load_env_vars())
        for key, val in os.environ.items():
            norm_key = key if self.case_sensitive else key.lower()
            if norm_key in ("public_demo", "admin_token", "public_runs"):
                if val == "" or val is None:
                    vars_dict[norm_key] = ""
        return vars_dict


class SecurityAwareDotEnvSource(DotEnvSettingsSource):
    """DotEnv source that preserves empty security flags so they fail closed."""

    def _read_env_files(self) -> Mapping[str, str | None]:
        vars_dict = dict(super()._read_env_files())
        env_files = self.env_file
        if env_files is None:
            return vars_dict
        if isinstance(env_files, (str, os.PathLike)):
            env_files = [env_files]
        for env_file in env_files:
            p = Path(env_file).expanduser()
            if p.is_file():
                raw = self._static_read_env_file(
                    p,
                    encoding=self.env_file_encoding,
                    case_sensitive=self.case_sensitive,
                    ignore_empty=False,
                    parse_none_str=self.env_parse_none_str,
                )
                for k, v in raw.items():
                    norm_key = k if self.case_sensitive else k.lower()
                    if norm_key in ("public_demo", "admin_token", "public_runs") and v == "":
                        vars_dict[norm_key] = ""
        return vars_dict


class Settings(BaseSettings):
    """Typed view of the process environment.

    Values come from the real environment first, then `.env`. Secrets have no
    defaults on purpose — an empty string means "not configured yet", which the
    modules that need them are responsible for checking.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    # Application
    app_name: str = "Vasooli"
    environment: str = "development"
    default_currency: str = "INR"

    # Public-demo guard
    public_demo: bool = False
    public_runs: bool = False
    admin_token: str = ""
    render: str = ""
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Orchestrator
    auto_orchestrate: bool = False

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        custom_env = SecurityAwareEnvSource(settings_cls)
        custom_dotenv = (
            SecurityAwareDotEnvSource(
                settings_cls,
                env_file=getattr(dotenv_settings, "env_file", None),
                env_file_encoding=getattr(dotenv_settings, "env_file_encoding", None),
                case_sensitive=getattr(dotenv_settings, "case_sensitive", None),
                env_parse_none_str=getattr(dotenv_settings, "env_parse_none_str", None),
            )
            if isinstance(dotenv_settings, DotEnvSettingsSource)
            else dotenv_settings
        )
        return init_settings, custom_env, custom_dotenv, file_secret_settings

    @field_validator("public_demo", "public_runs", mode="before")
    @classmethod
    def _parse_public_demo(cls, v: Any) -> bool:
        if v is None:
            return False
        if isinstance(v, str):
            s = v.strip().lower()
            if not s:
                return False
            if s in ("1", "true", "yes", "on", "t"):
                return True
            if s in ("0", "false", "no", "off", "f"):
                return False
            return False  # Fail closed for any invalid or unrecognised string
        if isinstance(v, bool):
            return v
        return bool(v)

    @field_validator("admin_token", mode="before")
    @classmethod
    def _parse_admin_token(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @property
    def is_production(self) -> bool:
        """Check whether the app is running in a production environment."""
        env_str = (self.environment or "").strip().lower()
        if env_str == "production":
            return True
        render_val = (self.render or os.environ.get("RENDER") or "").strip()
        if render_val:
            return True
        return False

    @model_validator(mode="after")
    def _validate_production_security(self) -> "Settings":
        """In production, refuse to start unless public_demo is True and admin_token is non-empty."""
        if self.is_production:
            if not self.public_demo:
                raise RuntimeError(
                    "Startup refused in production: 'public_demo' must be True (PUBLIC_DEMO=true)"
                )
            if not (self.admin_token or "").strip():
                raise RuntimeError(
                    "Startup refused in production: 'admin_token' must be non-empty (ADMIN_TOKEN must be configured)"
                )
        return self

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            if s.startswith("[") and s.endswith("]"):
                try:
                    loaded = json.loads(s)
                    if isinstance(loaded, list):
                        return [str(x).strip() for x in loaded if str(x).strip()]
                except Exception:
                    pass
            return [x.strip() for x in s.split(",") if x.strip()]
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x).strip()]
        return []

    # MongoDB
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "vasooli"

    # Google Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.6-flash"
    gemini_timeout_seconds: float = 20.0
    # The health probe gets its own, much shorter budget: `GET /` must stay fast
    # even when Gemini is hanging, so it cannot inherit the diagnosis timeout.
    gemini_health_timeout_seconds: float = 4.0
    # How long a reachability observation is trusted before it is re-checked.
    gemini_health_ttl_seconds: float = 60.0

    # Razorpay (test mode)
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # PayPal (sandbox) — Phase 2 / PayPal AI Hackathon
    paypal_env: str = "sandbox"
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_webhook_id: str = ""
    paypal_buyer_email: str = ""

    @property
    def paypal_environment(self) -> str:
        return self.paypal_env


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
