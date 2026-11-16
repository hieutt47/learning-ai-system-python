"""Environment-backed application configuration; no services are required at startup."""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    name: str = "Learning AI System API"
    environment: Literal["development", "test", "production"] = "development"
