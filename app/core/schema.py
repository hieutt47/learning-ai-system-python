"""Bounded, read-only V9 foundation check, not a full ORM/schema equivalence proof."""

from sqlalchemy import BigInteger, Boolean, DateTime, Engine, Numeric, String, inspect, text

# Only foundation columns used by the initial modules are checked. Future modules extend this.
REQUIRED = {
    "users": {
        "id": (BigInteger, False),
        "full_name": (String, False),
        "email": (String, False),
        "password_hash": (String, False),
        "role": (String, False),
        "is_active": (Boolean, False),
        "email_verified_at": (DateTime, True),
        "created_at": (DateTime, False),
        "updated_at": (DateTime, False),
    },
    "auth_sessions": {
        "id": (BigInteger, False),
        "user_id": (BigInteger, False),
        "refresh_token_hash": (String, False),
        "expires_at": (DateTime, False),
        "revoked_at": (DateTime, True),
        "created_at": (DateTime, False),
        "updated_at": (DateTime, False),
    },
    "categories": {
        "id": (BigInteger, False),
        "name": (String, False),
        "description": (String, True),
        "created_at": (DateTime, False),
        "updated_at": (DateTime, False),
    },
    "courses": {
        "id": (BigInteger, False),
        "teacher_id": (BigInteger, False),
        "category_id": (BigInteger, False),
        "title": (String, False),
        "description": (String, False),
        "price": (Numeric, False),
        "is_published": (Boolean, False),
        "archived_at": (DateTime, True),
        "created_at": (DateTime, False),
        "updated_at": (DateTime, False),
    },
}


def verify_schema(engine: Engine) -> None:
    if engine.dialect.name != "mysql":
        raise RuntimeError("Schema verification requires MySQL.")
    with engine.connect() as connection:
        inspector = inspect(connection)
        for table, expected in REQUIRED.items():
            if not inspector.has_table(table):
                raise RuntimeError(f"Missing foundation table: {table}.")
            columns = {column["name"]: column for column in inspector.get_columns(table)}
            for name, (kind, nullable) in expected.items():
                column = columns.get(name)
                # MySQL reflects BOOLEAN as TINYINT(1), not SQLAlchemy Boolean.
                boolean_match = (
                    kind is Boolean
                    and column is not None
                    and str(column["type"]).upper() in {"TINYINT", "TINYINT(1)", "BOOL", "BOOLEAN"}
                )
                if (
                    column is None
                    or not (isinstance(column["type"], kind) or boolean_match)
                    or column["nullable"] != nullable
                ):
                    raise RuntimeError(f"Foundation column mismatch: {table}.{name}.")
            if table == "courses":
                price = columns["price"]["type"]
                if (price.precision, price.scale) != (14, 2):
                    raise RuntimeError("Foundation column mismatch: courses.price precision/scale.")
            unique_column = {
                "users": "email",
                "auth_sessions": "refresh_token_hash",
                "categories": "name",
            }.get(table)
            if unique_column and not any(
                key["column_names"] == [unique_column]
                for key in inspector.get_unique_constraints(table)
            ):
                raise RuntimeError(f"Foundation unique key mismatch: {table}.{unique_column}.")
            foreign_keys = {
                (
                    tuple(key["constrained_columns"]),
                    key["referred_table"],
                    tuple(key["referred_columns"]),
                )
                for key in inspector.get_foreign_keys(table)
            }
            expected_foreign_keys = {
                "auth_sessions": {(("user_id",), "users", ("id",))},
                "courses": {
                    (("teacher_id",), "users", ("id",)),
                    (("category_id",), "categories", ("id",)),
                },
            }.get(table, set())
            if not expected_foreign_keys <= foreign_keys:
                raise RuntimeError(f"Foundation foreign key mismatch: {table}.")
            if inspector.get_pk_constraint(table)["constrained_columns"] != ["id"]:
                raise RuntimeError(f"Foundation primary key mismatch: {table}.")
        if not inspector.has_table("flyway_schema_history"):
            raise RuntimeError("Missing Flyway history; run the official runner first.")
        history = connection.execute(
            text("SELECT version, success FROM flyway_schema_history ORDER BY installed_rank")
        ).all()
        if [(str(row.version), bool(row.success)) for row in history] != [
            (str(version), True) for version in range(1, 10)
        ]:
            raise RuntimeError("Expected exactly successful Flyway versions 1 through 9.")
