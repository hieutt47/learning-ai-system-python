"""Explicit administration only: python -m app.core.db_cli info|validate|migrate|verify."""

import argparse

from app.core.database import DatabaseSettings, database_engine
from app.core.migrations import ACTIONS, run_flyway
from app.core.schema import verify_schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=(*ACTIONS, "verify"))
    args = parser.parse_args()
    try:
        settings = DatabaseSettings()
        if args.action == "verify":
            engine = database_engine(settings)
            try:
                verify_schema(engine)
            finally:
                engine.dispose()
        else:
            run_flyway(args.action, settings)
    except Exception:
        # Never print settings, DBAPI errors, or Flyway output containing connection secrets.
        print("Database command failed. Check configuration, runner and target schema securely.")
        return 1
    print(f"Database {args.action} succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
