"""Offline unit and API tests for assessment quiz authoring."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.domain import DomainError
from app.main import create_app
from app.modules.assessment.api import get_assessment_service
from app.modules.assessment.domain import (
    AssessmentService,
    LessonCourseContext,
    OptionInput,
    OptionRecord,
    QuestionInput,
    QuestionRecord,
    QuizRecord,
    QuizSummary,
)
from app.modules.identity.api.dependencies import current_actor
from app.modules.identity.domain.models import Actor

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)


class FakeUoW:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None or not self.committed:
            self.rolled_back = True

    def commit(self) -> None:
        self.committed = True


class FakeAssessmentRepo:
    def __init__(self) -> None:
        self.quizzes: dict[int, QuizRecord] = {}
        self.receipts: dict[tuple[int, int, str], tuple[str, str]] = {}
        self.enrolled: set[tuple[int, int]] = {(100, 20)}
        self.next_id = 1

    def lesson_context(self, lesson_id: int) -> LessonCourseContext | None:
        return LessonCourseContext(1, 100, 10) if lesson_id == 1 else None

    def quiz_context(self, quiz_id: int) -> LessonCourseContext | None:
        q = self.quizzes.get(quiz_id)
        return LessonCourseContext(q.lesson_id, 100, 10) if q else None

    def question_context(self, question_id: int) -> tuple[int, LessonCourseContext] | None:
        for q in self.quizzes.values():
            for qu in q.questions:
                if qu.id == question_id:
                    return q.id, LessonCourseContext(q.lesson_id, 100, 10)
        return None

    def is_enrolled(self, course_id: int, student_id: int) -> bool:
        return (course_id, student_id) in self.enrolled

    def generation_exists(self, generation_id: int, lesson_id: int) -> bool:
        return generation_id == 77 and lesson_id == 1

    def list_quizzes(self, lesson_id: int, *, published_only: bool, page: int, page_size: int):
        items = [
            QuizSummary(q.id, q.lesson_id, q.title, q.status, len(q.questions))
            for q in self.quizzes.values()
            if q.lesson_id == lesson_id and (not published_only or q.status == "PUBLISHED")
        ]
        return items[(page - 1) * page_size : page * page_size], len(items)

    def create_quiz(self, lesson_id: int, title: str, created_by: int, generation_id: int | None):
        qid = self.next_id
        self.next_id += 1
        rec = QuizRecord(qid, lesson_id, title, "DRAFT", NOW, [])
        self.quizzes[qid] = rec
        return rec

    def get_quiz(self, quiz_id: int, *, for_update: bool = False):
        return self.quizzes.get(quiz_id)

    def update_quiz_title(self, quiz_id: int, title: str):
        q = self.quizzes[quiz_id]
        rec = QuizRecord(q.id, q.lesson_id, title, q.status, q.created_at, q.questions)
        self.quizzes[quiz_id] = rec
        return rec

    def delete_quiz(self, quiz_id: int):
        self.quizzes.pop(quiz_id, None)

    def _to_record(self, qid: int, q: QuestionInput) -> QuestionRecord:
        opts = []
        for o in q.options:
            oid = self.next_id
            self.next_id += 1
            opts.append(OptionRecord(oid, o.content, o.is_correct, o.order_index))
        return QuestionRecord(
            qid, q.content, q.explanation, q.order_index, q.generation_id is not None, opts
        )

    def add_question(self, quiz_id: int, q: QuestionInput):
        qid = self.next_id
        self.next_id += 1
        rec = self._to_record(qid, q)
        quiz = self.quizzes[quiz_id]
        self.quizzes[quiz_id] = QuizRecord(
            quiz.id,
            quiz.lesson_id,
            quiz.title,
            quiz.status,
            quiz.created_at,
            [*quiz.questions, rec],
        )
        return rec

    def replace_question(self, question_id: int, q: QuestionInput):
        rec = self._to_record(question_id, q)
        for qid, quiz in self.quizzes.items():
            if any(item.id == question_id for item in quiz.questions):
                updated = [rec if item.id == question_id else item for item in quiz.questions]
                self.quizzes[qid] = QuizRecord(
                    quiz.id, quiz.lesson_id, quiz.title, quiz.status, quiz.created_at, updated
                )
        return rec

    def delete_question(self, question_id: int):
        for qid, quiz in self.quizzes.items():
            updated = [item for item in quiz.questions if item.id != question_id]
            self.quizzes[qid] = QuizRecord(
                quiz.id, quiz.lesson_id, quiz.title, quiz.status, quiz.created_at, updated
            )

    def replace_draft(self, quiz_id: int, title: str, questions: list[QuestionInput]):
        new_qs = []
        for q in questions:
            qid = q.id if q.id is not None else self.next_id
            if q.id is None:
                self.next_id += 1
            new_qs.append(self._to_record(qid, q))
        quiz = self.quizzes[quiz_id]
        rec = QuizRecord(quiz.id, quiz.lesson_id, title, quiz.status, quiz.created_at, new_qs)
        self.quizzes[quiz_id] = rec
        return rec

    def get_draft_receipt(self, quiz_id: int, actor_id: int, request_id: str):
        return self.receipts.get((quiz_id, actor_id, request_id))

    def save_draft_receipt(
        self, quiz_id: int, actor_id: int, request_id: str, payload_hash: str, response_json: str
    ):
        self.receipts[(quiz_id, actor_id, request_id)] = (payload_hash, response_json)

    def set_quiz_status(self, quiz_id: int, status: str):
        quiz = self.quizzes[quiz_id]
        rec = QuizRecord(
            quiz.id, quiz.lesson_id, quiz.title, status, quiz.created_at, quiz.questions
        )
        self.quizzes[quiz_id] = rec
        return rec


def _actor(uid: int, role: str) -> Actor:
    return Actor(uid, "User", f"u{uid}@example.invalid", role, True, NOW)


def test_quiz_authoring_lifecycle_validation_and_redaction():
    repo = FakeAssessmentRepo()
    svc = AssessmentService(repo, FakeUoW)
    teacher = _actor(10, "TEACHER")
    student = _actor(20, "STUDENT")

    quiz = svc.create_quiz(teacher, 1, "Quiz 1", generation_id=77)
    assert quiz.status == "DRAFT"

    # Empty quiz cannot publish
    with pytest.raises(DomainError) as exc_empty:
        svc.change_publication(teacher, quiz.id, "PUBLISHED")
    assert exc_empty.value.code == "INVALID_QUIZ"

    # Question must have >=2 options and exactly 1 correct
    with pytest.raises(DomainError):
        svc.add_question(
            teacher,
            quiz.id,
            QuestionInput("Q1", "Exp", 0, [OptionInput("Only one", True, 0)]),
        )

    svc.add_question(
        teacher,
        quiz.id,
        QuestionInput(
            "Q1",
            "Secret explanation",
            0,
            [OptionInput("A", True, 0), OptionInput("B", False, 1)],
            generation_id=77,
        ),
    )

    # Draft invisible to student
    with pytest.raises(DomainError) as exc_draft:
        svc.get_quiz(student, quiz.id)
    assert exc_draft.value.code == "NOT_FOUND"

    # Publish & close
    published = svc.change_publication(teacher, quiz.id, "PUBLISHED")
    assert published.status == "PUBLISHED"

    # Published is immutable
    with pytest.raises(DomainError) as exc_immut:
        svc.update_quiz(teacher, quiz.id, "Renamed")
    assert exc_immut.value.code == "INVALID_STATE"

    closed = svc.change_publication(teacher, quiz.id, "CLOSED")
    assert closed.status == "CLOSED"

    with pytest.raises(DomainError) as exc_repub:
        svc.change_publication(teacher, quiz.id, "PUBLISHED")
    assert exc_repub.value.code == "INVALID_STATE"


def test_quiz_api_draft_idempotency_and_student_redaction():
    repo = FakeAssessmentRepo()
    svc = AssessmentService(repo, FakeUoW)
    app = create_app(Settings(_env_file=None, environment="test"))
    current = {"actor": _actor(10, "TEACHER")}
    app.dependency_overrides[current_actor] = lambda: current["actor"]
    app.dependency_overrides[get_assessment_service] = lambda: svc

    with TestClient(app) as client:
        created = client.post("/api/v1/lessons/1/quizzes", json={"title": "Draft Quiz"})
        assert created.status_code == 201
        qid = created.json()["id"]

        req_id = str(uuid.uuid4())
        payload = {
            "request_id": req_id,
            "title": "Saved Quiz",
            "questions": [
                {
                    "content": "2+2=?",
                    "explanation": "Basic math",
                    "order_index": 0,
                    "options": [
                        {"content": "4", "is_correct": True, "order_index": 0},
                        {"content": "5", "is_correct": False, "order_index": 1},
                    ],
                }
            ],
        }
        r1 = client.put(f"/api/v1/quizzes/{qid}/draft", json=payload)
        assert r1.status_code == 200
        # Replay with identical request_id + payload returns exact stored response
        r2 = client.put(f"/api/v1/quizzes/{qid}/draft", json=payload)
        assert r2.status_code == 200
        assert r2.json() == r1.json()

        # Different payload with same request_id -> 409 INVALID_STATE
        bad_replay = {**payload, "title": "Tampered"}
        assert client.put(f"/api/v1/quizzes/{qid}/draft", json=bad_replay).status_code == 409

        # Publish
        assert (
            client.patch(
                f"/api/v1/quizzes/{qid}/publication", json={"status": "PUBLISHED"}
            ).status_code
            == 200
        )

        # Student sees no is_correct and no explanation
        current["actor"] = _actor(20, "STUDENT")
        stu = client.get(f"/api/v1/quizzes/{qid}")
        assert stu.status_code == 200
        stu_json = json.dumps(stu.json())
        assert "is_correct" not in stu_json
        assert "explanation" not in stu_json
        assert "Basic math" not in stu_json
