"""Parameterized queries for existing MySQL tables; never creates schema."""

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.catalog_course_dependencies import utc
from app.modules.enrollment.domain import CourseAccess, Membership


class SqlEnrollments:
    def __init__(self, session: Session):
        self.session = session

    def course(self, course_id: int, *, lock: bool = False) -> CourseAccess | None:
        row = (
            self.session.execute(
                text(
                    "SELECT id, teacher_id, is_published, archived_at, price FROM courses "
                    "WHERE id = :id" + (" FOR UPDATE" if lock else "")
                ),
                {"id": course_id},
            )
            .mappings()
            .first()
        )
        return (
            None
            if row is None
            else CourseAccess(
                row["id"],
                row["teacher_id"],
                bool(row["is_published"]),
                utc(row["archived_at"]),
                Decimal(row["price"]),
            )
        )

    def membership(self, course_id: int, student_id: int) -> Membership | None:
        # Current read, not an old REPEATABLE READ snapshot. The course is locked first.
        row = (
            self.session.execute(
                text(
                    "SELECT course_id, student_id, enrolled_at FROM course_members "
                    "WHERE course_id = :course AND student_id = :student FOR UPDATE"
                ),
                {"course": course_id, "student": student_id},
            )
            .mappings()
            .first()
        )
        return (
            None
            if row is None
            else Membership(
                row["course_id"],
                row["student_id"],
                utc(row["enrolled_at"]),
            )
        )

    def add(self, course_id: int, student_id: int) -> Membership:
        self.session.execute(
            text("INSERT INTO course_members (course_id, student_id) VALUES (:course, :student)"),
            {"course": course_id, "student": student_id},
        )
        return self.membership(course_id, student_id)

    def mine(self, student_id: int, page: int, page_size: int) -> tuple[list[dict], int]:
        rows = self.session.execute(
            text("""
                SELECT c.id, c.title, c.description, c.thumbnail_url, c.price, c.archived_at,
                       cat.id AS category_id, cat.name AS category_name,
                       u.id AS teacher_id, u.full_name AS teacher_name, m.enrolled_at,
                       (SELECT COUNT(*) FROM course_members cm
                        WHERE cm.course_id = c.id) AS total_students,
                       (SELECT COUNT(*) FROM lessons l
                        WHERE l.course_id = c.id) AS lesson_count
                FROM course_members m
                JOIN courses c ON c.id = m.course_id
                JOIN categories cat ON cat.id = c.category_id
                JOIN users u ON u.id = c.teacher_id
                WHERE m.student_id = :student
                ORDER BY m.enrolled_at DESC, m.id DESC
                LIMIT :limit OFFSET :offset
            """),
            {"student": student_id, "limit": page_size, "offset": (page - 1) * page_size},
        ).mappings()
        items = [
            {
                "id": str(row["id"]),
                "title": row["title"],
                "description": row["description"],
                "category": {"id": str(row["category_id"]), "name": row["category_name"]},
                "teacher": {"id": str(row["teacher_id"]), "full_name": row["teacher_name"]},
                "thumbnail_url": row["thumbnail_url"],
                "price": format(row["price"], ".2f"),
                "currency": "VND",
                "total_students": row["total_students"],
                "lesson_count": row["lesson_count"],
                "outline": None,
                "archived_at": utc(row["archived_at"]),
                "enrolled_at": utc(row["enrolled_at"]),
            }
            for row in rows
        ]
        total = self.session.execute(
            text("SELECT COUNT(*) FROM course_members WHERE student_id = :student"),
            {"student": student_id},
        ).scalar_one()
        return items, total

    def members(self, course_id: int, page: int, page_size: int) -> tuple[list[dict], int]:
        rows = self.session.execute(
            text("""
                SELECT m.course_id, m.student_id, u.full_name, m.enrolled_at
                FROM course_members m
                JOIN users u ON u.id = m.student_id
                WHERE m.course_id = :course
                ORDER BY m.enrolled_at DESC, m.id DESC
                LIMIT :limit OFFSET :offset
            """),
            {"course": course_id, "limit": page_size, "offset": (page - 1) * page_size},
        ).mappings()
        items = [
            {
                "course_id": str(row["course_id"]),
                "student_id": str(row["student_id"]),
                "full_name": row["full_name"],
                "enrolled_at": utc(row["enrolled_at"]),
            }
            for row in rows
        ]
        total = self.session.execute(
            text("SELECT COUNT(*) FROM course_members WHERE course_id = :course"),
            {"course": course_id},
        ).scalar_one()
        return items, total
