"""Parameterized MySQL catalog adapter; migrations own schema creation."""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.catalog_course_dependencies import utc
from app.modules.catalog.domain import Category


class SqlCategoryRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _row(row) -> Category | None:
        return (
            None
            if row is None
            else Category(
                row["id"],
                row["name"],
                row["description"],
                utc(row["created_at"]),
                utc(row["updated_at"]),
            )
        )

    def list(self, page: int, page_size: int) -> tuple[list[Category], int]:
        rows = self.session.execute(
            text(
                "SELECT * FROM categories ORDER BY created_at DESC, id DESC "
                "LIMIT :limit OFFSET :offset"
            ),
            {"limit": page_size, "offset": (page - 1) * page_size},
        ).mappings()
        items = [self._row(row) for row in rows]
        total = self.session.execute(text("SELECT COUNT(*) FROM categories")).scalar_one()
        return items, total

    def get(self, category_id: int, *, for_update: bool = False) -> Category | None:
        row = (
            self.session.execute(
                text(
                    "SELECT * FROM categories WHERE id = :id"
                    + (" FOR UPDATE" if for_update else "")
                ),
                {"id": category_id},
            )
            .mappings()
            .first()
        )
        return self._row(row)

    def name_exists(self, name: str, *, excluding_id: int | None = None) -> bool:
        return bool(
            self.session.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM categories WHERE name = :name "
                    "AND (:excluded IS NULL OR id <> :excluded))"
                ),
                {"name": name, "excluded": excluding_id},
            ).scalar_one()
        )

    def save(self, category: Category) -> Category:
        params = {"name": category.name, "description": category.description}
        if category.id is None:
            result = self.session.execute(
                text("INSERT INTO categories (name, description) VALUES (:name, :description)"),
                params,
            )
            category_id = result.lastrowid
        else:
            category_id = category.id
            self.session.execute(
                text(
                    "UPDATE categories SET name = :name, description = :description WHERE id = :id"
                ),
                {**params, "id": category_id},
            )
        return self.get(category_id)

    def has_courses(self, category_id: int) -> bool:
        return bool(
            self.session.execute(
                text("SELECT EXISTS(SELECT 1 FROM courses WHERE category_id = :id)"),
                {"id": category_id},
            ).scalar_one()
        )

    def delete(self, category_id: int) -> None:
        self.session.execute(text("DELETE FROM categories WHERE id = :id"), {"id": category_id})
