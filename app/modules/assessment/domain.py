"""Quiz authoring, validation, atomic draft save, and DRAFT -> PUBLISHED -> CLOSED lifecycle."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.core.domain import Actor, DomainError, Page, UnitOfWork, pagination, required_text


def _normalize_text(value: str) -> str:
    nfc = unicodedata.normalize("NFC", value)
    return " ".join(nfc.split()).lower()


def _check_len(value: str, field: str, max_len: int) -> str:
    trimmed = required_text(value, field, max_len)
    return trimmed


@dataclass(frozen=True)
class OptionInput:
    content: str
    is_correct: bool
    order_index: int


@dataclass(frozen=True)
class QuestionInput:
    content: str
    explanation: str | None
    order_index: int
    options: list[OptionInput]
    generation_id: int | None = None
    id: int | None = None


@dataclass(frozen=True)
class OptionRecord:
    id: int
    content: str
    is_correct: bool
    order_index: int


@dataclass(frozen=True)
class QuestionRecord:
    id: int
    content: str
    explanation: str | None
    order_index: int
    generated_by_ai: bool
    options: list[OptionRecord]


@dataclass(frozen=True)
class QuizRecord:
    id: int
    lesson_id: int
    title: str
    status: str
    created_at: datetime
    questions: list[QuestionRecord]


@dataclass(frozen=True)
class QuizSummary:
    id: int
    lesson_id: int
    title: str
    status: str
    question_count: int


@dataclass(frozen=True)
class LessonCourseContext:
    lesson_id: int
    course_id: int
    teacher_id: int


def validate_question_input(q: QuestionInput) -> QuestionInput:
    content = _check_len(q.content, "content", 4000)
    explanation: str | None = None
    if q.explanation is not None:
        if len(q.explanation) > 4000:
            raise DomainError("VALIDATION_ERROR", 400, "explanation must be at most 4000 chars.")
        explanation = q.explanation.strip() or None
    if q.order_index < 0:
        raise DomainError("VALIDATION_ERROR", 400, "order_index must be nonnegative.")
    if len(q.options) < 2 or len(q.options) > 6:
        raise DomainError("VALIDATION_ERROR", 400, "options must contain 2 to 6 items.")

    seen_contents: set[str] = set()
    seen_positions: set[int] = set()
    correct_count = 0
    cleaned_options: list[OptionInput] = []
    for opt in q.options:
        opt_content = _check_len(opt.content, "options.content", 1000)
        if opt.order_index < 0:
            raise DomainError("VALIDATION_ERROR", 400, "option order_index must be nonnegative.")
        norm = _normalize_text(opt_content)
        if norm in seen_contents:
            raise DomainError(
                "VALIDATION_ERROR", 400, "Option content must be unique after normalization."
            )
        seen_contents.add(norm)
        if opt.order_index in seen_positions:
            raise DomainError("INVALID_STATE", 409, "Option order_index must be unique.")
        seen_positions.add(opt.order_index)
        if opt.is_correct:
            correct_count += 1
        cleaned_options.append(OptionInput(opt_content, opt.is_correct, opt.order_index))

    if correct_count != 1:
        raise DomainError("VALIDATION_ERROR", 400, "Exactly one option must be correct.")

    return QuestionInput(
        content=content,
        explanation=explanation,
        order_index=q.order_index,
        options=cleaned_options,
        generation_id=q.generation_id,
        id=q.id,
    )


def draft_payload_hash(title: str, questions: list[QuestionInput]) -> str:
    canonical = {
        "title": title,
        "questions": [
            {
                "id": str(q.id) if q.id is not None else None,
                "content": q.content,
                "explanation": q.explanation,
                "order_index": q.order_index,
                "generation_id": str(q.generation_id) if q.generation_id is not None else None,
                "options": [
                    {
                        "content": o.content,
                        "is_correct": o.is_correct,
                        "order_index": o.order_index,
                    }
                    for o in q.options
                ],
            }
            for q in questions
        ],
    }
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class AssessmentRepository(Protocol):
    def lesson_context(self, lesson_id: int) -> LessonCourseContext | None: ...
    def quiz_context(self, quiz_id: int) -> LessonCourseContext | None: ...
    def question_context(self, question_id: int) -> tuple[int, LessonCourseContext] | None: ...
    def is_enrolled(self, course_id: int, student_id: int) -> bool: ...
    def generation_exists(self, generation_id: int, lesson_id: int) -> bool: ...
    def list_quizzes(
        self, lesson_id: int, *, published_only: bool, page: int, page_size: int
    ) -> tuple[list[QuizSummary], int]: ...
    def create_quiz(
        self, lesson_id: int, title: str, created_by: int, generation_id: int | None
    ) -> QuizRecord: ...
    def get_quiz(self, quiz_id: int, *, for_update: bool = False) -> QuizRecord | None: ...
    def update_quiz_title(self, quiz_id: int, title: str) -> QuizRecord: ...
    def delete_quiz(self, quiz_id: int) -> None: ...
    def add_question(self, quiz_id: int, q: QuestionInput) -> QuestionRecord: ...
    def replace_question(self, question_id: int, q: QuestionInput) -> QuestionRecord: ...
    def delete_question(self, question_id: int) -> None: ...
    def replace_draft(
        self, quiz_id: int, title: str, questions: list[QuestionInput]
    ) -> QuizRecord: ...
    def get_draft_receipt(
        self, quiz_id: int, actor_id: int, request_id: str
    ) -> tuple[str, str] | None: ...
    def save_draft_receipt(
        self, quiz_id: int, actor_id: int, request_id: str, payload_hash: str, response_json: str
    ) -> None: ...
    def set_quiz_status(self, quiz_id: int, status: str) -> QuizRecord: ...


class AssessmentService:
    def __init__(self, repository: AssessmentRepository, transaction: Callable[[], UnitOfWork]):
        self.repository, self.transaction = repository, transaction

    def _require_owner(self, actor: Actor, ctx: LessonCourseContext) -> None:
        if actor.role not in {"TEACHER", "ADMIN"}:
            raise DomainError("FORBIDDEN", 403, "Teacher or administrator access required.")
        if actor.role != "ADMIN" and ctx.teacher_id != actor.id:
            raise DomainError("FORBIDDEN", 403, "Course belongs to another teacher.")

    def _require_view(self, actor: Actor, ctx: LessonCourseContext) -> bool:
        """Return True if caller is owner/admin, False if enrolled student."""
        if actor.role in {"TEACHER", "ADMIN"}:
            if actor.role == "ADMIN" or ctx.teacher_id == actor.id:
                return True
            raise DomainError("FORBIDDEN", 403, "Course belongs to another teacher.")
        if actor.role == "STUDENT":
            if not self.repository.is_enrolled(ctx.course_id, actor.id):
                raise DomainError("ENROLLMENT_REQUIRED", 403, "Course enrollment is required.")
            return False
        raise DomainError("FORBIDDEN", 403, "Access denied.")

    def list_quizzes(
        self, actor: Actor, lesson_id: int, page: int = 1, page_size: int = 20
    ) -> Page[QuizSummary]:
        pagination(page, page_size)
        ctx = self.repository.lesson_context(lesson_id)
        if ctx is None:
            raise DomainError("NOT_FOUND", 404, "Lesson not found.")
        is_owner = self._require_view(actor, ctx)
        items, total = self.repository.list_quizzes(
            lesson_id, published_only=not is_owner, page=page, page_size=page_size
        )
        return Page(items, page, page_size, total)

    def create_quiz(
        self, actor: Actor, lesson_id: int, title: str, generation_id: int | None = None
    ) -> QuizRecord:
        clean_title = _check_len(title, "title", 200)
        with self.transaction() as uow:
            ctx = self.repository.lesson_context(lesson_id)
            if ctx is None:
                raise DomainError("NOT_FOUND", 404, "Lesson not found.")
            self._require_owner(actor, ctx)
            if generation_id is not None and not self.repository.generation_exists(
                generation_id, lesson_id
            ):
                raise DomainError("NOT_FOUND", 404, "AI generation not found for lesson.")
            quiz = self.repository.create_quiz(lesson_id, clean_title, actor.id, generation_id)
            uow.commit()
            return quiz

    def get_quiz(self, actor: Actor, quiz_id: int) -> tuple[QuizRecord, bool]:
        ctx = self.repository.quiz_context(quiz_id)
        if ctx is None:
            raise DomainError("NOT_FOUND", 404, "Quiz not found.")
        is_owner = self._require_view(actor, ctx)
        quiz = self.repository.get_quiz(quiz_id)
        if quiz is None:
            raise DomainError("NOT_FOUND", 404, "Quiz not found.")
        if not is_owner and quiz.status != "PUBLISHED":
            raise DomainError("NOT_FOUND", 404, "Quiz not found.")
        return quiz, is_owner

    def update_quiz(self, actor: Actor, quiz_id: int, title: str) -> QuizRecord:
        clean_title = _check_len(title, "title", 200)
        with self.transaction() as uow:
            ctx = self.repository.quiz_context(quiz_id)
            if ctx is None:
                raise DomainError("NOT_FOUND", 404, "Quiz not found.")
            self._require_owner(actor, ctx)
            quiz = self.repository.get_quiz(quiz_id, for_update=True)
            if quiz is None:
                raise DomainError("NOT_FOUND", 404, "Quiz not found.")
            if quiz.status != "DRAFT":
                raise DomainError("INVALID_STATE", 409, "Only DRAFT quizzes can be edited.")
            updated = self.repository.update_quiz_title(quiz_id, clean_title)
            uow.commit()
            return updated

    def delete_quiz(self, actor: Actor, quiz_id: int) -> None:
        with self.transaction() as uow:
            ctx = self.repository.quiz_context(quiz_id)
            if ctx is None:
                raise DomainError("NOT_FOUND", 404, "Quiz not found.")
            self._require_owner(actor, ctx)
            quiz = self.repository.get_quiz(quiz_id, for_update=True)
            if quiz is None:
                raise DomainError("NOT_FOUND", 404, "Quiz not found.")
            if quiz.status != "DRAFT":
                raise DomainError("INVALID_STATE", 409, "Only DRAFT quizzes can be deleted.")
            self.repository.delete_quiz(quiz_id)
            uow.commit()

    def add_question(self, actor: Actor, quiz_id: int, q: QuestionInput) -> QuestionRecord:
        cleaned = validate_question_input(q)
        with self.transaction() as uow:
            ctx = self.repository.quiz_context(quiz_id)
            if ctx is None:
                raise DomainError("NOT_FOUND", 404, "Quiz not found.")
            self._require_owner(actor, ctx)
            quiz = self.repository.get_quiz(quiz_id, for_update=True)
            if quiz is None:
                raise DomainError("NOT_FOUND", 404, "Quiz not found.")
            if quiz.status != "DRAFT":
                raise DomainError("INVALID_STATE", 409, "Only DRAFT quizzes can be modified.")
            if cleaned.generation_id is not None and not self.repository.generation_exists(
                cleaned.generation_id, ctx.lesson_id
            ):
                raise DomainError("NOT_FOUND", 404, "AI generation not found for lesson.")
            if any(existing.order_index == cleaned.order_index for existing in quiz.questions):
                raise DomainError("INVALID_STATE", 409, "Question order_index already exists.")
            created = self.repository.add_question(quiz_id, cleaned)
            uow.commit()
            return created

    def replace_question(self, actor: Actor, question_id: int, q: QuestionInput) -> QuestionRecord:
        cleaned = validate_question_input(q)
        with self.transaction() as uow:
            q_ctx = self.repository.question_context(question_id)
            if q_ctx is None:
                raise DomainError("NOT_FOUND", 404, "Question not found.")
            quiz_id, ctx = q_ctx
            self._require_owner(actor, ctx)
            quiz = self.repository.get_quiz(quiz_id, for_update=True)
            if quiz is None:
                raise DomainError("NOT_FOUND", 404, "Quiz not found.")
            if quiz.status != "DRAFT":
                raise DomainError("INVALID_STATE", 409, "Only DRAFT quizzes can be modified.")
            if any(
                existing.id != question_id and existing.order_index == cleaned.order_index
                for existing in quiz.questions
            ):
                raise DomainError("INVALID_STATE", 409, "Question order_index already exists.")
            updated = self.repository.replace_question(question_id, cleaned)
            uow.commit()
            return updated

    def delete_question(self, actor: Actor, question_id: int) -> None:
        with self.transaction() as uow:
            q_ctx = self.repository.question_context(question_id)
            if q_ctx is None:
                raise DomainError("NOT_FOUND", 404, "Question not found.")
            quiz_id, ctx = q_ctx
            self._require_owner(actor, ctx)
            quiz = self.repository.get_quiz(quiz_id, for_update=True)
            if quiz is None:
                raise DomainError("NOT_FOUND", 404, "Quiz not found.")
            if quiz.status != "DRAFT":
                raise DomainError("INVALID_STATE", 409, "Only DRAFT quizzes can be modified.")
            self.repository.delete_question(question_id)
            uow.commit()

    def save_draft(
        self,
        actor: Actor,
        quiz_id: int,
        request_id: str,
        title: str,
        questions: list[QuestionInput],
        serialize: Callable[[QuizRecord], str],
    ) -> str:
        clean_title = _check_len(title, "title", 200)
        if len(questions) > 200:
            raise DomainError("VALIDATION_ERROR", 400, "questions must have at most 200 items.")
        cleaned_questions = [validate_question_input(q) for q in questions]
        for idx, q in enumerate(cleaned_questions):
            if q.order_index != idx:
                raise DomainError(
                    "VALIDATION_ERROR", 400, "Question order_index must match array position."
                )
        payload_hash = draft_payload_hash(clean_title, cleaned_questions)

        with self.transaction() as uow:
            ctx = self.repository.quiz_context(quiz_id)
            if ctx is None:
                raise DomainError("NOT_FOUND", 404, "Quiz not found.")
            self._require_owner(actor, ctx)
            quiz = self.repository.get_quiz(quiz_id, for_update=True)
            if quiz is None:
                raise DomainError("NOT_FOUND", 404, "Quiz not found.")

            receipt = self.repository.get_draft_receipt(quiz_id, actor.id, request_id)
            if receipt is not None:
                stored_hash, stored_json = receipt
                if stored_hash != payload_hash:
                    raise DomainError(
                        "INVALID_STATE",
                        409,
                        "request_id was already used with a different payload.",
                    )
                return stored_json

            if quiz.status != "DRAFT":
                raise DomainError("INVALID_STATE", 409, "Only DRAFT quizzes can be saved.")

            existing_ids = {q.id for q in quiz.questions}
            seen_ids: set[int] = set()
            for q in cleaned_questions:
                if q.id is not None:
                    if q.id in seen_ids or q.id not in existing_ids:
                        raise DomainError("NOT_FOUND", 404, "Question not found in this quiz.")
                    seen_ids.add(q.id)
                if q.generation_id is not None and not self.repository.generation_exists(
                    q.generation_id, ctx.lesson_id
                ):
                    raise DomainError("NOT_FOUND", 404, "AI generation not found for lesson.")

            saved = self.repository.replace_draft(quiz_id, clean_title, cleaned_questions)
            response_json = serialize(saved)
            self.repository.save_draft_receipt(
                quiz_id, actor.id, request_id, payload_hash, response_json
            )
            uow.commit()
            return response_json

    def change_publication(self, actor: Actor, quiz_id: int, target_status: str) -> QuizRecord:
        if target_status not in {"PUBLISHED", "CLOSED"}:
            raise DomainError("VALIDATION_ERROR", 400, "status must be PUBLISHED or CLOSED.")
        with self.transaction() as uow:
            ctx = self.repository.quiz_context(quiz_id)
            if ctx is None:
                raise DomainError("NOT_FOUND", 404, "Quiz not found.")
            self._require_owner(actor, ctx)
            quiz = self.repository.get_quiz(quiz_id, for_update=True)
            if quiz is None:
                raise DomainError("NOT_FOUND", 404, "Quiz not found.")

            if quiz.status == target_status:
                return quiz
            if target_status == "PUBLISHED":
                if quiz.status != "DRAFT":
                    raise DomainError("INVALID_STATE", 409, "Only DRAFT quizzes can be published.")
                if len(quiz.questions) < 1:
                    raise DomainError(
                        "INVALID_QUIZ", 409, "Quiz must have at least one question to publish."
                    )
                for q in quiz.questions:
                    if len(q.options) < 2 or sum(1 for o in q.options if o.is_correct) != 1:
                        raise DomainError(
                            "INVALID_QUIZ",
                            409,
                            "Each question must have >=2 options and exactly 1 correct answer.",
                        )
            elif target_status == "CLOSED":
                if quiz.status != "PUBLISHED":
                    raise DomainError("INVALID_STATE", 409, "Only PUBLISHED quizzes can be closed.")
            updated = self.repository.set_quiz_status(quiz_id, target_status)
            uow.commit()
            return updated
