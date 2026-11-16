"""SQLAlchemy parameterized persistence for learning attempts and grading."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.catalog_course_dependencies import utc
from app.modules.learning.domain import (
    ActiveAttemptRace,
    AttemptRecord,
    AttemptSummary,
    QuestionWithOptions,
    QuizForAttempt,
    ReviewItem,
)

ATTEMPT_SELECT = """
SELECT a.id, a.quiz_id, q.title AS quiz_title, c.id AS course_id, c.teacher_id,
       a.student_id, a.status, a.score, a.total_questions, a.correct_count,
       a.started_at, a.submitted_at, a.version
FROM quiz_attempts a
JOIN quizzes q ON q.id = a.quiz_id
JOIN lessons l ON l.id = q.lesson_id
JOIN courses c ON c.id = l.course_id
"""


class SqlLearningRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _attempt(row) -> AttemptRecord | None:
        if row is None:
            return None
        return AttemptRecord(
            id=row["id"],
            quiz_id=row["quiz_id"],
            quiz_title=row["quiz_title"],
            course_id=row["course_id"],
            teacher_id=row["teacher_id"],
            student_id=row["student_id"],
            status=row["status"],
            score=Decimal(row["score"]) if row["score"] is not None else None,
            total_questions=row["total_questions"],
            correct_count=row["correct_count"],
            started_at=utc(row["started_at"]),
            submitted_at=utc(row["submitted_at"]),
            version=row["version"],
        )

    def get_quiz_for_attempt(self, quiz_id: int) -> QuizForAttempt | None:
        row = (
            self.session.execute(
                text(
                    "SELECT q.id, q.lesson_id, c.id AS course_id, c.teacher_id, q.title, q.status, "
                    "(SELECT COUNT(*) FROM questions qu WHERE qu.quiz_id = q.id) AS question_count "
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
            else QuizForAttempt(
                id=row["id"],
                lesson_id=row["lesson_id"],
                course_id=row["course_id"],
                teacher_id=row["teacher_id"],
                title=row["title"],
                status=row["status"],
                question_count=row["question_count"],
            )
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

    def get_active_attempt(self, quiz_id: int, student_id: int) -> AttemptRecord | None:
        row = (
            self.session.execute(
                text(
                    ATTEMPT_SELECT
                    + " WHERE a.quiz_id = :q AND a.student_id = :s AND a.status = 'IN_PROGRESS'"
                ),
                {"q": quiz_id, "s": student_id},
            )
            .mappings()
            .first()
        )
        return self._attempt(row)

    def create_attempt(self, quiz_id: int, student_id: int, total_questions: int) -> AttemptRecord:
        try:
            res = self.session.execute(
                text(
                    "INSERT INTO quiz_attempts "
                    "(quiz_id, student_id, status, total_questions, version) "
                    "VALUES (:q, :s, 'IN_PROGRESS', :tq, 0)"
                ),
                {"q": quiz_id, "s": student_id, "tq": total_questions},
            )
        except IntegrityError as exc:
            self.session.rollback()
            raise ActiveAttemptRace() from exc
        return self.get_attempt(res.lastrowid)

    def get_attempt(self, attempt_id: int, *, for_update: bool = False) -> AttemptRecord | None:
        row = (
            self.session.execute(
                text(ATTEMPT_SELECT + " WHERE a.id = :id" + (" FOR UPDATE" if for_update else "")),
                {"id": attempt_id},
            )
            .mappings()
            .first()
        )
        return self._attempt(row)

    def get_quiz_questions(self, quiz_id: int) -> list[QuestionWithOptions]:
        q_rows = self.session.execute(
            text(
                "SELECT id, content, explanation FROM questions "
                "WHERE quiz_id = :quiz_id ORDER BY order_index ASC, id ASC"
            ),
            {"quiz_id": quiz_id},
        ).mappings()
        o_rows = self.session.execute(
            text(
                "SELECT o.id, o.question_id, o.content, o.is_correct "
                "FROM question_options o "
                "JOIN questions qu ON qu.id = o.question_id "
                "WHERE qu.quiz_id = :quiz_id ORDER BY o.order_index ASC, o.id ASC"
            ),
            {"quiz_id": quiz_id},
        ).mappings()
        opts_by_q: dict[int, list[tuple[int, str]]] = {}
        correct_by_q: dict[int, int] = {}
        for o in o_rows:
            opts_by_q.setdefault(o["question_id"], []).append((o["id"], o["content"]))
            if bool(o["is_correct"]):
                correct_by_q[o["question_id"]] = o["id"]
        return [
            QuestionWithOptions(
                id=q["id"],
                content=q["content"],
                explanation=q["explanation"],
                correct_option_id=correct_by_q[q["id"]],
                options=opts_by_q.get(q["id"], []),
            )
            for q in q_rows
        ]

    def save_submission(
        self,
        attempt_id: int,
        expected_version: int,
        score: Decimal,
        correct_count: int,
        answers: list[tuple[int, int | None, bool]],
    ) -> AttemptRecord:
        res = self.session.execute(
            text(
                "UPDATE quiz_attempts "
                "SET status = 'SUBMITTED', score = :score, correct_count = :cc, "
                "submitted_at = UTC_TIMESTAMP(6), version = version + 1 "
                "WHERE id = :id AND version = :ver AND status = 'IN_PROGRESS'"
            ),
            {"score": score, "cc": correct_count, "id": attempt_id, "ver": expected_version},
        )
        if res.rowcount != 1:
            raise RuntimeError("Optimistic lock mismatch on attempt submit.")
        for qid, oid, is_correct in answers:
            self.session.execute(
                text(
                    "INSERT INTO student_answers "
                    "(attempt_id, question_id, selected_option_id, is_correct) "
                    "VALUES (:aid, :qid, :oid, :ok)"
                ),
                {"aid": attempt_id, "qid": qid, "oid": oid, "ok": is_correct},
            )
        return self.get_attempt(attempt_id)

    def get_attempt_review(self, attempt_id: int, quiz_id: int) -> list[ReviewItem]:
        questions = self.get_quiz_questions(quiz_id)
        ans_rows = self.session.execute(
            text(
                "SELECT question_id, selected_option_id, is_correct "
                "FROM student_answers WHERE attempt_id = :aid"
            ),
            {"aid": attempt_id},
        ).mappings()
        ans_map = {
            r["question_id"]: (r["selected_option_id"], bool(r["is_correct"])) for r in ans_rows
        }
        return [
            ReviewItem(
                question_id=q.id,
                question_content=q.content,
                selected_option_id=ans_map.get(q.id, (None, False))[0],
                is_correct=ans_map.get(q.id, (None, False))[1],
                correct_option_id=q.correct_option_id,
                explanation=q.explanation,
                options=q.options,
            )
            for q in questions
        ]

    def list_history(
        self,
        student_id: int,
        *,
        quiz_id: int | None,
        course_id: int | None,
        page: int,
        page_size: int,
    ) -> tuple[list[AttemptSummary], int]:
        clauses = ["a.student_id = :student_id"]
        params: dict = {
            "student_id": student_id,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        if quiz_id is not None:
            clauses.append("a.quiz_id = :quiz_id")
            params["quiz_id"] = quiz_id
        if course_id is not None:
            clauses.append("c.id = :course_id")
            params["course_id"] = course_id
        where = " WHERE " + " AND ".join(clauses)
        rows = self.session.execute(
            text(
                "SELECT a.id, a.quiz_id, q.title AS quiz_title, "
                "c.id AS course_id, c.title AS course_title, "
                "a.status, a.score, a.started_at, a.submitted_at "
                "FROM quiz_attempts a "
                "JOIN quizzes q ON q.id = a.quiz_id "
                "JOIN lessons l ON l.id = q.lesson_id "
                "JOIN courses c ON c.id = l.course_id "
                + where
                + " ORDER BY a.started_at DESC, a.id DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        ).mappings()
        items = [
            AttemptSummary(
                id=r["id"],
                quiz_id=r["quiz_id"],
                quiz_title=r["quiz_title"],
                course_id=r["course_id"],
                course_title=r["course_title"],
                status=r["status"],
                score=Decimal(r["score"]) if r["score"] is not None else None,
                started_at=utc(r["started_at"]),
                submitted_at=utc(r["submitted_at"]),
            )
            for r in rows
        ]
        total = self.session.execute(
            text(
                "SELECT COUNT(*) FROM quiz_attempts a "
                "JOIN quizzes q ON q.id = a.quiz_id "
                "JOIN lessons l ON l.id = q.lesson_id "
                "JOIN courses c ON c.id = l.course_id " + where
            ),
            params,
        ).scalar_one()
        return items, total
