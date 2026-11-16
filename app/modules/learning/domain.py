"""Attempt lifecycle, atomic server-side grading, and account-isolated history."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from app.core.domain import Actor, DomainError, Page, UnitOfWork, pagination


@dataclass(frozen=True)
class QuizForAttempt:
    id: int
    lesson_id: int
    course_id: int
    teacher_id: int
    title: str
    status: str
    question_count: int


@dataclass(frozen=True)
class QuestionWithOptions:
    id: int
    content: str
    explanation: str | None
    correct_option_id: int
    options: list[tuple[int, str]]  # (option_id, content)


@dataclass(frozen=True)
class AttemptRecord:
    id: int
    quiz_id: int
    quiz_title: str
    course_id: int
    teacher_id: int
    student_id: int
    status: str
    score: Decimal | None
    total_questions: int
    correct_count: int | None
    started_at: datetime
    submitted_at: datetime | None
    version: int


@dataclass(frozen=True)
class ReviewItem:
    question_id: int
    question_content: str
    selected_option_id: int | None
    is_correct: bool
    correct_option_id: int
    explanation: str | None
    options: list[tuple[int, str]]


@dataclass(frozen=True)
class AttemptResult:
    attempt: AttemptRecord
    review: list[ReviewItem]


@dataclass(frozen=True)
class AttemptSummary:
    id: int
    quiz_id: int
    quiz_title: str
    course_id: int
    course_title: str
    status: str
    score: Decimal | None
    started_at: datetime
    submitted_at: datetime | None


@dataclass(frozen=True)
class AnswerInput:
    question_id: int
    selected_option_id: int | None


class ActiveAttemptRace(Exception):
    """Raised when DB unique constraint uq_quiz_attempts_active catches a concurrent start."""


class LearningRepository(Protocol):
    def get_quiz_for_attempt(self, quiz_id: int) -> QuizForAttempt | None: ...
    def is_enrolled(self, course_id: int, student_id: int) -> bool: ...
    def get_active_attempt(self, quiz_id: int, student_id: int) -> AttemptRecord | None: ...
    def create_attempt(
        self, quiz_id: int, student_id: int, total_questions: int
    ) -> AttemptRecord: ...
    def get_attempt(self, attempt_id: int, *, for_update: bool = False) -> AttemptRecord | None: ...
    def get_quiz_questions(self, quiz_id: int) -> list[QuestionWithOptions]: ...
    def save_submission(
        self,
        attempt_id: int,
        expected_version: int,
        score: Decimal,
        correct_count: int,
        answers: list[tuple[int, int | None, bool]],
    ) -> AttemptRecord: ...
    def get_attempt_review(self, attempt_id: int, quiz_id: int) -> list[ReviewItem]: ...
    def list_history(
        self,
        student_id: int,
        *,
        quiz_id: int | None,
        course_id: int | None,
        page: int,
        page_size: int,
    ) -> tuple[list[AttemptSummary], int]: ...


def compute_score(correct_count: int, total_questions: int) -> Decimal:
    raw = (Decimal(correct_count) * Decimal(100)) / Decimal(total_questions)
    return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class LearningService:
    def __init__(self, repository: LearningRepository, transaction: Callable[[], UnitOfWork]):
        self.repository, self.transaction = repository, transaction

    @staticmethod
    def _student(actor: Actor) -> None:
        if actor.role != "STUDENT":
            raise DomainError("FORBIDDEN", 403, "Only students can start or submit attempts.")

    def start(self, actor: Actor, quiz_id: int) -> tuple[AttemptRecord, bool]:
        self._student(actor)
        try:
            with self.transaction() as uow:
                quiz = self.repository.get_quiz_for_attempt(quiz_id)
                if quiz is None:
                    raise DomainError("NOT_FOUND", 404, "Quiz not found.")
                if not self.repository.is_enrolled(quiz.course_id, actor.id):
                    raise DomainError("ENROLLMENT_REQUIRED", 403, "Course enrollment is required.")
                if quiz.status == "DRAFT":
                    raise DomainError("NOT_FOUND", 404, "Quiz not found.")
                existing = self.repository.get_active_attempt(quiz_id, actor.id)
                if existing is not None:
                    return existing, False
                if quiz.status != "PUBLISHED":
                    raise DomainError(
                        "INVALID_STATE", 409, "Quiz is closed and cannot accept new attempts."
                    )
                if quiz.question_count < 1:
                    raise DomainError("INVALID_QUIZ", 409, "Quiz has no questions.")
                created = self.repository.create_attempt(quiz_id, actor.id, quiz.question_count)
                uow.commit()
                return created, True
        except ActiveAttemptRace:
            with self.transaction() as uow:
                existing = self.repository.get_active_attempt(quiz_id, actor.id)
                if existing is not None:
                    return existing, False
                raise DomainError(
                    "INVALID_STATE", 409, "Concurrent attempt conflict; please retry."
                ) from None

    def submit(self, actor: Actor, attempt_id: int, answers: list[AnswerInput]) -> AttemptResult:
        self._student(actor)
        seen_questions: set[int] = set()
        answer_map: dict[int, int | None] = {}
        for ans in answers:
            if ans.question_id in seen_questions:
                raise DomainError("INVALID_ANSWER", 400, "Duplicate question_id in answers.")
            seen_questions.add(ans.question_id)
            answer_map[ans.question_id] = ans.selected_option_id

        with self.transaction() as uow:
            attempt = self.repository.get_attempt(attempt_id, for_update=True)
            if attempt is None:
                raise DomainError("NOT_FOUND", 404, "Attempt not found.")
            if attempt.student_id != actor.id:
                raise DomainError("FORBIDDEN", 403, "Attempt belongs to another student.")
            if attempt.status == "SUBMITTED":
                review = self.repository.get_attempt_review(attempt_id, attempt.quiz_id)
                return AttemptResult(attempt, review)

            questions = self.repository.get_quiz_questions(attempt.quiz_id)
            q_by_id = {q.id: q for q in questions}
            for qid, oid in answer_map.items():
                if qid not in q_by_id:
                    raise DomainError("INVALID_ANSWER", 400, "Question does not belong to quiz.")
                if oid is not None:
                    valid_oids = {opt_id for opt_id, _ in q_by_id[qid].options}
                    if oid not in valid_oids:
                        raise DomainError(
                            "INVALID_ANSWER", 400, "Option does not belong to question."
                        )

            graded_rows: list[tuple[int, int | None, bool]] = []
            correct_count = 0
            for q in questions:
                selected = answer_map.get(q.id)
                is_correct = selected is not None and selected == q.correct_option_id
                if is_correct:
                    correct_count += 1
                graded_rows.append((q.id, selected, is_correct))

            score = compute_score(correct_count, len(questions))
            saved = self.repository.save_submission(
                attempt_id, attempt.version, score, correct_count, graded_rows
            )
            review = self.repository.get_attempt_review(attempt_id, attempt.quiz_id)
            uow.commit()
            return AttemptResult(saved, review)

    def get_attempt(self, actor: Actor, attempt_id: int) -> AttemptRecord | AttemptResult:
        attempt = self.repository.get_attempt(attempt_id)
        if attempt is None:
            raise DomainError("NOT_FOUND", 404, "Attempt not found.")
        if actor.role == "STUDENT":
            if attempt.student_id != actor.id:
                raise DomainError("FORBIDDEN", 403, "Attempt belongs to another student.")
        elif actor.role == "TEACHER":
            if attempt.teacher_id != actor.id:
                raise DomainError("FORBIDDEN", 403, "Course belongs to another teacher.")
        elif actor.role != "ADMIN":
            raise DomainError("FORBIDDEN", 403, "Access denied.")

        if attempt.status == "IN_PROGRESS":
            return attempt
        review = self.repository.get_attempt_review(attempt_id, attempt.quiz_id)
        return AttemptResult(attempt, review)

    def history(
        self,
        actor: Actor,
        *,
        quiz_id: int | None = None,
        course_id: int | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[AttemptSummary]:
        self._student(actor)
        pagination(page, page_size)
        items, total = self.repository.list_history(
            actor.id, quiz_id=quiz_id, course_id=course_id, page=page, page_size=page_size
        )
        return Page(items, page, page_size, total)
