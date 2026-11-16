from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import Numeric, String
from sqlalchemy.dialects.mysql import TINYINT

from app.core.database import DatabaseSettings, database_engine, parse_database_url, session_scope
from app.core.migrations import MIGRATIONS, ROOT, run_flyway
from app.core.schema import REQUIRED, verify_schema

URL = "mysql+pymysql://runner:private-secret@127.0.0.1:13307/foundation_test"
HASHES = [
    "e601751b54518f407c3d1f043104a5b4ad01d842015bb2d22d663cd27ef11843",
    "960d8df8b8c9d84692cbc95991ebe267bb2579a73b23e17665bb7f0781fe7c51",
    "8ecc938ee67574fd1bcafe4eac1d7416073eec1046fb3ff2fbdea84b925fd0de",
    "db81d6c4082a7c06025f0320b33dd410c7cdd473ca61f24b36ab06d48fe8ebd2",
    "97a8192b9a4033b07d544713875b8c085e900259c99ee44531917ad4932260e3",
    "568cd906da4f7b3cdea2fc6332b6a26ea9105a4bde999ad34877b0592d477054",
    "ba112adecbdb71b33e509eea9eb62a4f3f88dbf3d4247ea89a1d075b8a51681a",
    "3f2ecd4752598175d504acd5d5530151fc503e0862b081923dfbd7d57b5bbda6",
    "d384a7836fc834eaad9864a83ffc87c1ce1302c3eb456452a404db6d20d4a0f4",
]


def settings():
    return DatabaseSettings(_env_file=None, database_url=URL)


def test_canonical_migration_bytes():
    files = sorted(MIGRATIONS.glob("V*.sql"))
    assert [int(path.name.split("__")[0][1:]) for path in files] == list(range(1, 10))
    assert [sha256(path.read_bytes()).hexdigest() for path in files] == HASHES


@pytest.mark.parametrize(
    "url",
    ["", "sqlite://", "mysql://u:p@host/db", "mysql+pymysql://host/db", URL + "?local_infile=1"],
)
def test_reject_unsupported_or_incomplete_urls(url):
    with pytest.raises(ValueError):
        parse_database_url(url)


def test_required_secret_settings(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        DatabaseSettings(_env_file=None)
    assert "private-secret" not in repr(settings())
    with pytest.raises(ValidationError) as error:
        DatabaseSettings(_env_file=None, database_url="invalid-private-secret")
    assert "invalid-private-secret" not in str(error.value)


def test_engine_is_lazy():
    engine = database_engine(settings())
    assert engine.dialect.name == "mysql"
    assert engine.dialect.driver == "pymysql"
    assert engine.hide_parameters
    engine.dispose()


@pytest.mark.parametrize("failure", [False, True])
def test_session_closes_and_transaction_handles_success_or_error(failure):
    with patch("app.core.database.Session") as factory:
        session = factory.return_value.__enter__.return_value
        context = session.begin.return_value
        if failure:
            with pytest.raises(RuntimeError), session_scope(MagicMock()):
                raise RuntimeError("rollback")
            assert context.__exit__.call_args.args[0] is RuntimeError
        else:
            with session_scope(MagicMock()) as actual:
                assert actual is session
            assert context.__exit__.call_args.args == (None, None, None)
        factory.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("action", ["info", "validate", "migrate"])
def test_flyway_delegates_safely(action, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLYWAY_EXECUTABLE", "custom-flyway.cmd")
    monkeypatch.setenv("FLYWAY_CLEAN_DISABLED", "false")
    monkeypatch.setenv("FLYWAY_TARGET", "1")
    with (
        patch("app.core.migrations.shutil.which", return_value="C:/tools/flyway.cmd") as which,
        patch(
            "app.core.migrations.subprocess.run", return_value=SimpleNamespace(returncode=0)
        ) as run,
    ):
        run_flyway(action, settings())
    which.assert_called_once_with("custom-flyway.cmd")
    args, kwargs = run.call_args
    command = args[0]
    assert command[-1] == action
    assert "private-secret" not in str(command)
    assert "-cleanDisabled=true" in command
    assert "-baselineOnMigrate=false" in command
    assert "-configFiles=" in command
    assert "-placeholderReplacement=false" in command
    assert kwargs["cwd"] == ROOT
    assert kwargs["env"]["FLYWAY_PASSWORD"] == "private-secret"
    assert "FLYWAY_TARGET" not in kwargs["env"]
    assert "FLYWAY_CLEAN_DISABLED" not in kwargs["env"]
    assert kwargs["capture_output"]


@pytest.mark.parametrize("action", ["clean", "repair", "baseline", "migrate -target=1"])
def test_flyway_rejects_unsafe_actions(action):
    with patch("app.core.migrations.subprocess.run") as run, pytest.raises(ValueError):
        run_flyway(action, settings())
    run.assert_not_called()


def test_missing_runner():
    with (
        patch("app.core.migrations.shutil.which", return_value=None),
        pytest.raises(RuntimeError, match="FLYWAY_EXECUTABLE"),
    ):
        run_flyway("info", settings())


def test_runner_failure_does_not_echo_output():
    with (
        patch("app.core.migrations.shutil.which", return_value="flyway"),
        patch(
            "app.core.migrations.subprocess.run",
            return_value=SimpleNamespace(returncode=7, stderr="private-secret"),
        ),
        pytest.raises(RuntimeError, match="exit 7") as error,
    ):
        run_flyway("migrate", settings())
    assert "private-secret" not in str(error.value)


def schema_engine():
    engine = MagicMock()
    engine.dialect.name = "mysql"
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.all.return_value = [
        SimpleNamespace(version=str(v), success=True) for v in range(1, 10)
    ]
    inspector = MagicMock()
    inspector.has_table.return_value = True
    inspector.get_pk_constraint.return_value = {"constrained_columns": ["id"]}
    inspector.get_columns.side_effect = lambda table: [
        {
            "name": name,
            "nullable": nullable,
            "type": (
                TINYINT()
                if name in {"is_active", "is_published"}
                else Numeric(14, 2)
                if kind is Numeric
                else kind()
            ),
        }
        for name, (kind, nullable) in REQUIRED[table].items()
    ]
    inspector.get_unique_constraints.side_effect = lambda table: [
        {"column_names": [name]}
        for name in {
            "users": ["email"],
            "auth_sessions": ["refresh_token_hash"],
            "categories": ["name"],
            "courses": [],
        }[table]
    ]
    inspector.get_foreign_keys.side_effect = lambda table: [
        {"constrained_columns": [column], "referred_table": target, "referred_columns": ["id"]}
        for column, target in {
            "users": [],
            "categories": [],
            "auth_sessions": [("user_id", "users")],
            "courses": [("teacher_id", "users"), ("category_id", "categories")],
        }[table]
    ]
    return engine, inspector


def test_schema_verification_read_only():
    engine, inspector = schema_engine()
    with patch("app.core.schema.inspect", return_value=inspector):
        verify_schema(engine)
    connection = engine.connect.return_value.__enter__.return_value
    assert str(connection.execute.call_args.args[0]).startswith("SELECT")


@pytest.mark.parametrize("fault", ["missing_table", "column", "pk", "history"])
def test_schema_rejects_incompatible_foundation(fault):
    engine, inspector = schema_engine()
    if fault == "missing_table":
        inspector.has_table.return_value = False
    elif fault == "column":
        inspector.get_columns.side_effect = None
        inspector.get_columns.return_value = [{"name": "id", "type": String(), "nullable": False}]
    elif fault == "pk":
        inspector.get_pk_constraint.return_value = {"constrained_columns": []}
    else:
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.return_value.all.return_value = []
    with patch("app.core.schema.inspect", return_value=inspector), pytest.raises(RuntimeError):
        verify_schema(engine)
