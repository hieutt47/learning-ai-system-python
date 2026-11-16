"""Course SQL adapter and narrow category lookup; no cross-module repository access."""

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.catalog_course_dependencies import utc
from app.modules.course.domain import Course

COURSE_SELECT = """
SELECT c.id, c.title, c.description, c.category_id, c.teacher_id, c.price,
       c.is_published, c.archived_at, c.thumbnail_url, c.created_at, c.updated_at,
       cat.name AS category_name, u.full_name AS teacher_name,
       (SELECT COUNT(*) FROM lessons l WHERE l.course_id = c.id) AS lesson_count,
       (SELECT COUNT(*) FROM course_members m WHERE m.course_id = c.id) AS total_students
FROM courses c JOIN categories cat ON cat.id = c.category_id
JOIN users u ON u.id = c.teacher_id
"""


class SqlCourseCategoryLookup:
    def __init__(self, session: Session):
        self.session = session

    def exists(self, category_id: int, *, for_update: bool = False) -> bool:
        return (
            self.session.execute(
                text(
                    "SELECT id FROM categories WHERE id = :id"
                    + (" FOR UPDATE" if for_update else "")
                ),
                {"id": category_id},
            ).first()
            is not None
        )


class SqlCourseRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _row(row) -> Course | None:
        if row is None:
            return None
        values = dict(row)
        for key in ("created_at", "updated_at", "archived_at"):
            values[key] = utc(values[key])
        values["is_published"] = bool(values["is_published"])
        values["price"] = Decimal(values["price"])
        return Course(**values)

    def list(self, page: int, page_size: int, *, teacher_id: int | None):
        where = " WHERE c.teacher_id = :teacher_id" if teacher_id is not None else ""
        params = {"teacher_id": teacher_id, "limit": page_size, "offset": (page - 1) * page_size}
        rows = self.session.execute(
            text(
                COURSE_SELECT
                + where
                + " ORDER BY c.created_at DESC, c.id DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        ).mappings()
        items = [self._row(row) for row in rows]
        total = self.session.execute(
            text("SELECT COUNT(*) FROM courses c" + where), {"teacher_id": teacher_id}
        ).scalar_one()
        return items, total

    def get(self, course_id: int, *, for_update: bool = False) -> Course | None:
        # A current locking read must supply the business fields too: a later
        # snapshot read could otherwise return stale state under REPEATABLE READ.
        row = (
            self.session.execute(
                text(COURSE_SELECT + " WHERE c.id = :id" + (" FOR UPDATE" if for_update else "")),
                {"id": course_id},
            )
            .mappings()
            .first()
        )
        return self._row(row)

    def save(self, course: Course) -> Course:
        params = {
            "title": course.title,
            "description": course.description,
            "category_id": course.category_id,
            "price": course.price,
        }
        if course.id is None:
            result = self.session.execute(
                text(
                    "INSERT INTO courses (title, description, category_id, teacher_id, price) "
                    "VALUES (:title, :description, :category_id, :teacher_id, :price)"
                ),
                {**params, "teacher_id": course.teacher_id},
            )
            course_id = result.lastrowid
        else:
            course_id = course.id
            self.session.execute(
                text(
                    "UPDATE courses SET title = :title, description = :description, "
                    "category_id = :category_id, price = :price WHERE id = :id"
                ),
                {**params, "id": course_id},
            )
        return self.get(course_id)

    def dependencies(self, course_id: int) -> tuple[str, ...]:
        # All direct course foreign keys in V1-V9; descendants are protected by lessons.
        return tuple(
            table
            for table in ("lessons", "course_members")
            if self.session.execute(
                text(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE course_id = :id)"),
                {"id": course_id},
            ).scalar_one()
        )

    def delete(self, course_id: int) -> None:
        self.session.execute(text("DELETE FROM courses WHERE id = :id"), {"id": course_id})
