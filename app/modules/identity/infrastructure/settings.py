"""Identity-only settings; absence is handled lazily by auth dependencies."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTH_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    jwt_secret: SecretStr
    cookie_secure: bool = False
    environment: Literal["development", "test", "production"] = "development"
    cors_origins: list[str] = []

    @field_validator("jwt_secret")
    @classmethod
    def strong_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("AUTH_JWT_SECRET must contain at least 32 bytes")
        return value

    @field_validator("cors_origins")
    @classmethod
    def exact_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path
                or "*" in origin
            ):
                raise ValueError("CORS requires explicit HTTP(S) origins without paths")
        return origins

    @model_validator(mode="after")
    def secure_production_cookie(self) -> "AuthSettings":
        if self.environment == "production" and not self.cookie_secure:
            raise ValueError("Production requires Secure auth cookies")
        return self
