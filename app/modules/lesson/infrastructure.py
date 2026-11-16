"""Parameterized adapters for the reused V1-V9 MySQL schema; no DDL."""

from dataclasses import asdict

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.catalog_course_dependencies import utc
from app.modules.lesson.domain import CourseAccess, Lesson

LESSON_SELECT = """
SELECT l.*,
 (SELECT COUNT(*) FROM lesson_documents d WHERE d.lesson_id = l.id) AS document_count,
 (SELECT COUNT(*) FROM quizzes q WHERE q.lesson_id = l.id) AS quiz_count,
 (SELECT COUNT(*) FROM flashcards f WHERE f.lesson_id = l.id) AS flashcard_count,
 (SELECT COUNT(*) FROM ai_generations a WHERE a.lesson_id = l.id) AS generation_count
FROM lessons l
"""


class SqlCourseAccess:
    """Implements the lesson's narrow access port without invoking course repositories."""

    def __init__(self, session: Session):
        self.session = session

    def get(
        self, course_id: int, actor_id: int, *, for_update: bool = False
    ) -> CourseAccess | None:
        row = (
            self.session.execute(
                text(
                    "SELECT id, teacher_id, is_published, archived_at FROM courses WHERE id = :id"
                    + (" FOR UPDATE" if for_update else "")
                ),
                {"id": course_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        values = dict(row)
        values["archived_at"] = utc(values["archived_at"])
        values["is_published"] = bool(values["is_published"])
        # Locking reads avoid a prior REPEATABLE READ snapshot hiding memberships.
        if for_update:
            member = self.session.execute(
                text(
                    "SELECT student_id FROM course_members WHERE course_id = :id LIMIT 1 FOR UPDATE"
                ),
                {"id": course_id},
            ).first()
            values.update(enrolled=False, has_members=member is not None)
        else:
            values["enrolled"] = bool(
                self.session.execute(
                    text(
                        "SELECT EXISTS(SELECT 1 FROM course_members "
                        "WHERE course_id = :id AND student_id = :actor)"
                    ),
                    {"id": course_id, "actor": actor_id},
                ).scalar_one()
            )
            values["has_members"] = bool(
                self.session.execute(
                    text("SELECT EXISTS(SELECT 1 FROM course_members WHERE course_id = :id)"),
                    {"id": course_id},
                ).scalar_one()
            )
        return CourseAccess(**values)


class SqlLessonRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _row(row) -> Lesson | None:
        if row is None:
            return None
        values = dict(row)
        for field in ("created_at", "updated_at"):
            values[field] = utc(values[field])
        return Lesson(**values)

    def list(self, course_id: int) -> list[Lesson]:
        return [
            self._row(row)
            for row in self.session.execute(
                text(
                    LESSON_SELECT + " WHERE l.course_id = :id ORDER BY l.order_index ASC, l.id ASC"
                ),
                {"id": course_id},
            ).mappings()
        ]

    def get(self, lesson_id: int, *, for_update: bool = False) -> Lesson | None:
        return self._row(
            self.session.execute(
                text(LESSON_SELECT + " WHERE l.id = :id" + (" FOR UPDATE" if for_update else "")),
                {"id": lesson_id},
            )
            .mappings()
            .first()
        )

    def next_order(self, course_id: int) -> int:
        # Caller already locked the parent course, including for an empty lesson set.
        # Current row read, not MAX(snapshot), stays current after earlier plain reads.
        row = self.session.execute(
            text(
                "SELECT order_index FROM lessons WHERE course_id = :id "
                "ORDER BY order_index DESC LIMIT 1 FOR UPDATE"
            ),
            {"id": course_id},
        ).first()
        return row[0] + 1 if row is not None else 1

    def save(self, lesson: Lesson) -> Lesson:
        params = {
            key: value
            for key, value in asdict(lesson).items()
            if key in {"id", "course_id", "title", "video_url", "transcript_text", "order_index"}
        }
        if lesson.id is None:
            result = self.session.execute(
                text(
                    "INSERT INTO lessons "
                    "(course_id, title, video_url, transcript_text, order_index) "
                    "VALUES (:course_id, :title, :video_url, :transcript_text, :order_index)"
                ),
                params,
            )
            lesson_id = result.lastrowid
        else:
            lesson_id = lesson.id
            self.session.execute(
                text(
                    "UPDATE lessons SET title = :title, video_url = :video_url, "
                    "transcript_text = :transcript_text WHERE id = :id"
                ),
                params,
            )
        return self.get(lesson_id, for_update=True)

    def dependencies(self, lesson_id: int) -> bool:
        # Current reads are essential after update/delete's initial snapshot lookup.
        return any(
            self.session.execute(
                text(f"SELECT id FROM {table} WHERE lesson_id = :id LIMIT 1 FOR UPDATE"),
                {"id": lesson_id},
            ).first()
            is not None
            for table in ("lesson_documents", "quizzes", "flashcards", "ai_generations")
        )

    def delete(self, lesson_id: int) -> None:
        # No cascade/document cleanup: restrictive FK races map to safe 409 via UoW.
        self.session.execute(text("DELETE FROM lessons WHERE id = :id"), {"id": lesson_id})
