"""Delegate SQL and history handling exclusively to the official Flyway CLI."""

import os
import shutil
import subprocess
from pathlib import Path

from app.core.database import DatabaseSettings, parse_database_url

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "database" / "migrations"
ACTIONS = ("info", "validate", "migrate")


def run_flyway(action: str, settings: DatabaseSettings) -> None:
    if action not in ACTIONS:
        raise ValueError("Only info, validate and migrate are supported.")
    executable = shutil.which(os.environ.get("FLYWAY_EXECUTABLE") or "flyway")
    if not executable:
        raise RuntimeError("Official Flyway CLI not found; configure FLYWAY_EXECUTABLE.")
    url = parse_database_url(settings.database_url.get_secret_value())
    host = f"[{url.host}]" if ":" in url.host else url.host
    # Never inherit Flyway targets/credentials from a developer's shell.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("FLYWAY_")}
    env.update(
        FLYWAY_URL=(
            f"jdbc:mysql://{host}:{url.port or 3306}/{url.database}"
            "?characterEncoding=UTF-8&serverTimezone=UTC"
        ),
        FLYWAY_USER=url.username,
        FLYWAY_PASSWORD=url.password,
    )
    command = [
        executable,
        "-configFiles=",
        f"-locations=filesystem:{MIGRATIONS.as_posix()}",
        f"-schemas={url.database}",
        f"-defaultSchema={url.database}",
        "-table=flyway_schema_history",
        "-encoding=UTF-8",
        "-placeholderReplacement=false",
        "-createSchemas=false",
        "-cleanDisabled=true",
        "-baselineOnMigrate=false",
        "-outOfOrder=false",
        "-validateOnMigrate=true",
        "-validateMigrationNaming=true",
        "-failOnMissingLocations=true",
        action,
    ]
    try:
        result = subprocess.run(
            command, cwd=ROOT, env=env, capture_output=True, timeout=300, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError(
            "Flyway could not execute or timed out; inspect the disposable target."
        ) from None
    if result.returncode:
        # Driver/CLI output may contain URLs or credentials. Do not relay it or repair history.
        raise RuntimeError(
            f"Flyway {action} failed (exit {result.returncode}); no automatic repair was attempted."
        )
