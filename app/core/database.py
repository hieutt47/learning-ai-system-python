"""Explicit MySQL connection/session setup; importing this module never connects."""

import re
from collections.abc import Iterator
from contextlib import contextmanager

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    database_url: SecretStr

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        parse_database_url(value.get_secret_value())
        return value


def parse_database_url(value: str) -> URL:
    """Accept only an explicit network MySQL target; never expose URL parse errors."""
    try:
        url = make_url(value)
        valid = (
            url.drivername == "mysql+pymysql"
            and bool(url.host)
            and bool(url.username)
            and bool(url.password)
            and bool(url.database)
            and re.fullmatch(r"[A-Za-z0-9_]+", url.database or "")
            and (url.port is None or 1 <= url.port <= 65535)
            and all(key == "charset" and val == "utf8mb4" for key, val in url.query.items())
        )
        if valid:
            return url
    except (ValueError, TypeError, ArgumentError):
        pass
    raise ValueError(
        "DATABASE_URL must be mysql+pymysql with explicit host, user, password and database; "
        "only the optional charset=utf8mb4 query is supported."
    ) from None


def database_engine(settings: DatabaseSettings) -> Engine:
    url = parse_database_url(settings.database_url.get_secret_value())
    return create_engine(
        url.update_query_dict({"charset": "utf8mb4"}),
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"init_command": "SET time_zone = '+00:00'"},
    )


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """One short transaction: commit on success, rollback on failure, always close."""
    with Session(engine) as session, session.begin():
        yield session
