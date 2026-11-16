"""Opt-in tests for a precreated EMPTY disposable MySQL database. Never use application DBs."""

import os

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.database import DatabaseSettings, database_engine, parse_database_url, session_scope
from app.core.migrations import run_flyway
from app.core.schema import verify_schema


def require_test_database(value: str) -> DatabaseSettings:
    url = parse_database_url(value)
    if not (url.database.startswith("test_") or url.database.endswith("_test")):
        raise ValueError("TEST_DATABASE_URL database must start test_ or end _test.")
    return DatabaseSettings(_env_file=None, database_url=value)


@pytest.mark.parametrize("name", ["production", "test", "mytestdb"])
def test_database_name_guard(name):
    with pytest.raises(ValueError):
        require_test_database(f"mysql+pymysql://u:p@localhost/{name}")


@pytest.fixture(scope="module")
def mysql_engine():
    if os.environ.get("RUN_MYSQL_TESTS") != "1":
        pytest.skip(
            "Real MySQL pending: requires RUN_MYSQL_TESTS=1 and disposable TEST_DATABASE_URL."
        )
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("Explicit TEST_DATABASE_URL is required; no application-URL fallback.")
    settings = require_test_database(value)
    engine = database_engine(settings)
    try:
        if inspect(engine).get_table_names():
            pytest.fail(
                "Integration tests require an empty disposable database; nothing was migrated."
            )
        run_flyway("migrate", settings)
        run_flyway("validate", settings)
        yield engine, settings
    finally:
        engine.dispose()
    # Keep the schema/history for inspection. No DROP, clean, repair, or automatic reset.


@pytest.mark.mysql
def test_mysql_migration_history_and_repeat_are_stable(mysql_engine):
    engine, settings = mysql_engine
    verify_schema(engine)
    with engine.connect() as connection:
        before = connection.execute(text("SELECT * FROM flyway_schema_history")).all()
    run_flyway("migrate", settings)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT * FROM flyway_schema_history")).all() == before
    verify_schema(engine)


@pytest.mark.mysql
def test_mysql_session_commit_rollback_and_unique_constraint(mysql_engine):
    engine, _ = mysql_engine
    insert = text(
        "INSERT INTO users (full_name, email, password_hash) "
        "VALUES ('DB fixture', :email, 'not-a-real-password-hash')"
    )
    with session_scope(engine) as session:
        session.execute(insert, {"email": "committed@example.invalid"})
    with pytest.raises(RuntimeError), session_scope(engine) as session:
        session.execute(insert, {"email": "rolled-back@example.invalid"})
        raise RuntimeError("test rollback")
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT COUNT(*) FROM users WHERE email='committed@example.invalid'")
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT COUNT(*) FROM users WHERE email='rolled-back@example.invalid'")
            )
            == 0
        )
    with pytest.raises(IntegrityError), session_scope(engine) as session:
        session.execute(insert, {"email": "committed@example.invalid"})
