"""SQLAlchemy parameterized persistence for assessment (MySQL 8 V1-V9)."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.catalog_course_dependencies import utc
from app.modules.assessment.domain import (
    LessonCourseContext,
    OptionRecord,
    QuestionInput,
    QuestionRecord,
    QuizRecord,
    QuizSummary,
)


class SqlAssessmentRepository:
    def __init__(self, session: Session):
        self.session = session

    def lesson_context(self, lesson_id: int) -> LessonCourseContext | None:
        row = (
            self.session.execute(
                text(
                    "SELECT l.id AS lesson_id, c.id AS course_id, c.teacher_id "
                    "FROM lessons l JOIN courses c ON c.id = l.course_id "
                    "WHERE l.id = :id"
                ),
                {"id": lesson_id},
            )
            .mappings()
            .first()
        )
        return (
            None
            if row is None
            else LessonCourseContext(row["lesson_id"], row["course_id"], row["teacher_id"])
        )

    def quiz_context(self, quiz_id: int) -> LessonCourseContext | None:
        row = (
            self.session.execute(
                text(
                    "SELECT l.id AS lesson_id, c.id AS course_id, c.teacher_id "
                    "FROM quizzes q "
                    "JOIN lessons l ON l.id = q.lesson_id "
                    "JOIN courses c ON c.id = l.course_id "
                    "WHERE q.id = :id"
                ),
                {"id": quiz_id},
            )
            .mappings()
            .first()
        )
        return (
            None
            if row is None
            else LessonCourseContext(row["lesson_id"], row["course_id"], row["teacher_id"])
        )

    def question_context(self, question_id: int) -> tuple[int, LessonCourseContext] | None:
        row = (
            self.session.execute(
                text(
                    "SELECT qu.quiz_id, l.id AS lesson_id, c.id AS course_id, c.teacher_id "
                    "FROM questions qu "
                    "JOIN quizzes q ON q.id = qu.quiz_id "
                    "JOIN lessons l ON l.id = q.lesson_id "
                    "JOIN courses c ON c.id = l.course_id "
                    "WHERE qu.id = :id"
                ),
                {"id": question_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return row["quiz_id"], LessonCourseContext(
            row["lesson_id"], row["course_id"], row["teacher_id"]
        )

    def is_enrolled(self, course_id: int, student_id: int) -> bool:
        return (
            self.session.execute(
                text(
                    "SELECT 1 FROM course_members WHERE course_id = :c AND student_id = :s LIMIT 1"
                ),
                {"c": course_id, "s": student_id},
            ).first()
            is not None
        )

    def generation_exists(self, generation_id: int, lesson_id: int) -> bool:
        return (
            self.session.execute(
                text("SELECT 1 FROM ai_generations WHERE id = :g AND lesson_id = :l LIMIT 1"),
                {"g": generation_id, "l": lesson_id},
            ).first()
            is not None
        )

    def list_quizzes(
        self, lesson_id: int, *, published_only: bool, page: int, page_size: int
    ) -> tuple[list[QuizSummary], int]:
        where = "WHERE q.lesson_id = :lesson_id" + (
            " AND q.status = 'PUBLISHED'" if published_only else ""
        )
        params = {"lesson_id": lesson_id, "limit": page_size, "offset": (page - 1) * page_size}
        rows = self.session.execute(
            text(
                "SELECT q.id, q.lesson_id, q.title, q.status, "
                "(SELECT COUNT(*) FROM questions qu WHERE qu.quiz_id = q.id) AS question_count "
                f"FROM quizzes q {where} "
                "ORDER BY q.created_at DESC, q.id DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        ).mappings()
        items = [
            QuizSummary(
                id=r["id"],
                lesson_id=r["lesson_id"],
                title=r["title"],
                status=r["status"],
                question_count=r["question_count"],
            )
            for r in rows
        ]
        total = self.session.execute(
            text(f"SELECT COUNT(*) FROM quizzes q {where}"),
            {"lesson_id": lesson_id},
        ).scalar_one()
        return items, total

    def create_quiz(
        self, lesson_id: int, title: str, created_by: int, generation_id: int | None
    ) -> QuizRecord:
        res = self.session.execute(
            text(
                "INSERT INTO quizzes (lesson_id, title, status, generation_id, created_by) "
                "VALUES (:lesson_id, :title, 'DRAFT', :generation_id, :created_by)"
            ),
            {
                "lesson_id": lesson_id,
                "title": title,
                "generation_id": generation_id,
                "created_by": created_by,
            },
        )
        return self.get_quiz(res.lastrowid)

    def get_quiz(self, quiz_id: int, *, for_update: bool = False) -> QuizRecord | None:
        row = (
            self.session.execute(
                text(
                    "SELECT id, lesson_id, title, status, created_at FROM quizzes WHERE id = :id"
                    + (" FOR UPDATE" if for_update else "")
                ),
                {"id": quiz_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        q_rows = self.session.execute(
            text(
                "SELECT id, content, explanation, order_index, generated_by_ai "
                "FROM questions WHERE quiz_id = :quiz_id ORDER BY order_index ASC, id ASC"
            ),
            {"quiz_id": quiz_id},
        ).mappings()
        questions_raw = list(q_rows)
        options_by_q: dict[int, list[OptionRecord]] = {}
        if questions_raw:
            o_rows = self.session.execute(
                text(
                    "SELECT o.id, o.question_id, o.content, o.is_correct, o.order_index "
                    "FROM question_options o "
                    "JOIN questions qu ON qu.id = o.question_id "
                    "WHERE qu.quiz_id = :quiz_id ORDER BY o.order_index ASC, o.id ASC"
                ),
                {"quiz_id": quiz_id},
            ).mappings()
            for o in o_rows:
                options_by_q.setdefault(o["question_id"], []).append(
                    OptionRecord(
                        id=o["id"],
                        content=o["content"],
                        is_correct=bool(o["is_correct"]),
                        order_index=o["order_index"],
                    )
                )
        questions = [
            QuestionRecord(
                id=q["id"],
                content=q["content"],
                explanation=q["explanation"],
                order_index=q["order_index"],
                generated_by_ai=bool(q["generated_by_ai"]),
                options=options_by_q.get(q["id"], []),
            )
            for q in questions_raw
        ]
        return QuizRecord(
            id=row["id"],
            lesson_id=row["lesson_id"],
            title=row["title"],
            status=row["status"],
            created_at=utc(row["created_at"]),
            questions=questions,
        )

    def update_quiz_title(self, quiz_id: int, title: str) -> QuizRecord:
        self.session.execute(
            text("UPDATE quizzes SET title = :title WHERE id = :id"),
            {"title": title, "id": quiz_id},
        )
        return self.get_quiz(quiz_id)

    def delete_quiz(self, quiz_id: int) -> None:
        self.session.execute(
            text(
                "DELETE o FROM question_options o "
                "JOIN questions q ON q.id = o.question_id WHERE q.quiz_id = :quiz_id"
            ),
            {"quiz_id": quiz_id},
        )
        self.session.execute(text("DELETE FROM questions WHERE quiz_id = :id"), {"id": quiz_id})
        self.session.execute(text("DELETE FROM quizzes WHERE id = :id"), {"id": quiz_id})

    def _get_question(self, question_id: int) -> QuestionRecord:
        q = (
            self.session.execute(
                text(
                    "SELECT id, content, explanation, order_index, generated_by_ai "
                    "FROM questions WHERE id = :id"
                ),
                {"id": question_id},
            )
            .mappings()
            .one()
        )
        o_rows = self.session.execute(
            text(
                "SELECT id, content, is_correct, order_index "
                "FROM question_options WHERE question_id = :qid ORDER BY order_index ASC, id ASC"
            ),
            {"qid": question_id},
        ).mappings()
        return QuestionRecord(
            id=q["id"],
            content=q["content"],
            explanation=q["explanation"],
            order_index=q["order_index"],
            generated_by_ai=bool(q["generated_by_ai"]),
            options=[
                OptionRecord(
                    id=o["id"],
                    content=o["content"],
                    is_correct=bool(o["is_correct"]),
                    order_index=o["order_index"],
                )
                for o in o_rows
            ],
        )

    def _insert_options(self, question_id: int, q: QuestionInput) -> None:
        for opt in q.options:
            self.session.execute(
                text(
                    "INSERT INTO question_options (question_id, content, is_correct, order_index) "
                    "VALUES (:qid, :content, :is_correct, :order_index)"
                ),
                {
                    "qid": question_id,
                    "content": opt.content,
                    "is_correct": opt.is_correct,
                    "order_index": opt.order_index,
                },
            )

    def add_question(self, quiz_id: int, q: QuestionInput) -> QuestionRecord:
        res = self.session.execute(
            text(
                "INSERT INTO questions "
                "(quiz_id, content, question_type, explanation, order_index, "
                "generated_by_ai, generation_id) "
                "VALUES (:quiz_id, :content, 'MULTIPLE_CHOICE', :explanation, :order_index, "
                ":gen_ai, :gen_id)"
            ),
            {
                "quiz_id": quiz_id,
                "content": q.content,
                "explanation": q.explanation,
                "order_index": q.order_index,
                "gen_ai": q.generation_id is not None,
                "gen_id": q.generation_id,
            },
        )
        qid = res.lastrowid
        self._insert_options(qid, q)
        return self._get_question(qid)

    def replace_question(self, question_id: int, q: QuestionInput) -> QuestionRecord:
        self.session.execute(
            text(
                "UPDATE questions SET content = :content, explanation = :explanation, "
                "order_index = :order_index WHERE id = :id"
            ),
            {
                "content": q.content,
                "explanation": q.explanation,
                "order_index": q.order_index,
                "id": question_id,
            },
        )
        self.session.execute(
            text("DELETE FROM question_options WHERE question_id = :qid"),
            {"qid": question_id},
        )
        self._insert_options(question_id, q)
        return self._get_question(question_id)

    def delete_question(self, question_id: int) -> None:
        self.session.execute(
            text("DELETE FROM question_options WHERE question_id = :qid"),
            {"qid": question_id},
        )
        self.session.execute(text("DELETE FROM questions WHERE id = :id"), {"id": question_id})

    def replace_draft(self, quiz_id: int, title: str, questions: list[QuestionInput]) -> QuizRecord:
        self.session.execute(
            text("UPDATE quizzes SET title = :title WHERE id = :id"),
            {"title": title, "id": quiz_id},
        )
        self.session.execute(
            text(
                "DELETE o FROM question_options o "
                "JOIN questions q ON q.id = o.question_id WHERE q.quiz_id = :quiz_id"
            ),
            {"quiz_id": quiz_id},
        )
        keep_ids = {q.id for q in questions if q.id is not None}
        existing_rows = self.session.execute(
            text("SELECT id FROM questions WHERE quiz_id = :quiz_id"),
            {"quiz_id": quiz_id},
        ).scalars()
        for existing_id in existing_rows:
            if existing_id not in keep_ids:
                self.session.execute(
                    text("DELETE FROM questions WHERE id = :id"),
                    {"id": existing_id},
                )
        # Shift remaining question order_index to avoid UNIQUE(quiz_id, order_index) collisions.
        self.session.execute(
            text(
                "UPDATE questions SET order_index = order_index + 100000 WHERE quiz_id = :quiz_id"
            ),
            {"quiz_id": quiz_id},
        )
        for q in questions:
            if q.id is not None:
                self.session.execute(
                    text(
                        "UPDATE questions SET content = :content, explanation = :explanation, "
                        "order_index = :order_index WHERE id = :id"
                    ),
                    {
                        "content": q.content,
                        "explanation": q.explanation,
                        "order_index": q.order_index,
                        "id": q.id,
                    },
                )
                self._insert_options(q.id, q)
            else:
                self.add_question(quiz_id, q)
        return self.get_quiz(quiz_id)

    def get_draft_receipt(
        self, quiz_id: int, actor_id: int, request_id: str
    ) -> tuple[str, str] | None:
        row = (
            self.session.execute(
                text(
                    "SELECT payload_hash, response_json FROM quiz_draft_save_receipts "
                    "WHERE quiz_id = :quiz_id AND actor_id = :actor_id AND request_id = :req_id"
                ),
                {"quiz_id": quiz_id, "actor_id": actor_id, "req_id": request_id},
            )
            .mappings()
            .first()
        )
        return None if row is None else (row["payload_hash"], row["response_json"])

    def save_draft_receipt(
        self, quiz_id: int, actor_id: int, request_id: str, payload_hash: str, response_json: str
    ) -> None:
        self.session.execute(
            text(
                "INSERT INTO quiz_draft_save_receipts "
                "(quiz_id, actor_id, request_id, payload_hash, response_json) "
                "VALUES (:quiz_id, :actor_id, :req_id, :payload_hash, :response_json)"
            ),
            {
                "quiz_id": quiz_id,
                "actor_id": actor_id,
                "req_id": request_id,
                "payload_hash": payload_hash,
                "response_json": response_json,
            },
        )

    def set_quiz_status(self, quiz_id: int, status: str) -> QuizRecord:
        if status == "PUBLISHED":
            self.session.execute(
                text(
                    "UPDATE quizzes SET status = 'PUBLISHED', "
                    "published_at = COALESCE(published_at, UTC_TIMESTAMP(6)) WHERE id = :id"
                ),
                {"id": quiz_id},
            )
        elif status == "CLOSED":
            self.session.execute(
                text(
                    "UPDATE quizzes SET status = 'CLOSED', "
                    "closed_at = COALESCE(closed_at, UTC_TIMESTAMP(6)) WHERE id = :id"
                ),
                {"id": quiz_id},
            )
        return self.get_quiz(quiz_id)
