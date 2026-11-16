"""Offline unit and API tests for learning attempts, grading, and history."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.domain import DomainError
from app.main import create_app
from app.modules.identity.api.dependencies import current_actor
from app.modules.identity.domain.models import Actor
from app.modules.learning.api import get_learning_service
from app.modules.learning.domain import (
    AnswerInput,
    AttemptRecord,
    AttemptSummary,
    LearningService,
    QuestionWithOptions,
    QuizForAttempt,
    ReviewItem,
)

NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=UTC)


class FakeUoW:
    def __init__(self) -> None:
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        pass

    def commit(self) -> None:
        self.committed = True


class FakeLearningRepo:
    def __init__(self) -> None:
        self.quizzes = {
            1: QuizForAttempt(1, 10, 100, 10, "Quiz 1", "PUBLISHED", 3),
        }
        self.enrolled = {(100, 20), (100, 21)}
        self.attempts: dict[int, AttemptRecord] = {}
        self.reviews: dict[int, list[ReviewItem]] = {}
        self.questions = {
            1: [
                QuestionWithOptions(101, "Q1", "Exp1", 1001, [(1001, "A"), (1002, "B")]),
                QuestionWithOptions(102, "Q2", "Exp2", 1003, [(1003, "C"), (1004, "D")]),
                QuestionWithOptions(103, "Q3", None, 1005, [(1005, "E"), (1006, "F")]),
            ]
        }
        self.next_id = 1

    def get_quiz_for_attempt(self, quiz_id: int):
        return self.quizzes.get(quiz_id)

    def is_enrolled(self, course_id: int, student_id: int) -> bool:
        return (course_id, student_id) in self.enrolled

    def get_active_attempt(self, quiz_id: int, student_id: int):
        for a in self.attempts.values():
            if a.quiz_id == quiz_id and a.student_id == student_id and a.status == "IN_PROGRESS":
                return a
        return None

    def create_attempt(self, quiz_id: int, student_id: int, total_questions: int):
        aid = self.next_id
        self.next_id += 1
        rec = AttemptRecord(
            aid,
            quiz_id,
            "Quiz 1",
            100,
            10,
            student_id,
            "IN_PROGRESS",
            None,
            total_questions,
            None,
            NOW,
            None,
            0,
        )
        self.attempts[aid] = rec
        return rec

    def get_attempt(self, attempt_id: int, *, for_update: bool = False):
        return self.attempts.get(attempt_id)

    def get_quiz_questions(self, quiz_id: int):
        return self.questions[quiz_id]

    def save_submission(
        self, attempt_id: int, expected_version: int, score: Decimal, correct_count: int, answers
    ):
        a = self.attempts[attempt_id]
        rec = AttemptRecord(
            a.id,
            a.quiz_id,
            a.quiz_title,
            a.course_id,
            a.teacher_id,
            a.student_id,
            "SUBMITTED",
            score,
            a.total_questions,
            correct_count,
            a.started_at,
            NOW,
            expected_version + 1,
        )
        self.attempts[attempt_id] = rec
        q_map = {q.id: q for q in self.questions[a.quiz_id]}
        self.reviews[attempt_id] = [
            ReviewItem(
                qid,
                q_map[qid].content,
                oid,
                ok,
                q_map[qid].correct_option_id,
                q_map[qid].explanation,
                q_map[qid].options,
            )
            for qid, oid, ok in answers
        ]
        return rec

    def get_attempt_review(self, attempt_id: int, quiz_id: int):
        return self.reviews[attempt_id]

    def list_history(self, student_id: int, *, quiz_id, course_id, page, page_size):
        items = [
            AttemptSummary(
                a.id,
                a.quiz_id,
                a.quiz_title,
                a.course_id,
                "Course",
                a.status,
                a.score,
                a.started_at,
                a.submitted_at,
            )
            for a in self.attempts.values()
            if a.student_id == student_id
        ]
        return items[(page - 1) * page_size : page * page_size], len(items)


def _actor(uid: int, role: str) -> Actor:
    return Actor(uid, "User", f"u{uid}@example.invalid", role, True, NOW)


def test_learning_grading_rounding_retry_and_closed_quiz():
    repo = FakeLearningRepo()
    svc = LearningService(repo, FakeUoW)
    s1 = _actor(20, "STUDENT")
    s2 = _actor(21, "STUDENT")

    a1, created = svc.start(s1, 1)
    assert created is True
    a1_retry, created2 = svc.start(s1, 1)
    assert created2 is False
    assert a1_retry.id == a1.id

    # Close quiz while s1 is IN_PROGRESS -> s2 cannot start, s1 can still submit
    repo.quizzes[1] = QuizForAttempt(1, 10, 100, 10, "Quiz 1", "CLOSED", 3)
    with pytest.raises(DomainError) as exc_closed:
        svc.start(s2, 1)
    assert exc_closed.value.code == "INVALID_STATE"

    # 1 of 3 correct, 1 wrong, 1 blank -> round(100/3, 2) = 33.33
    res1 = svc.submit(s1, a1.id, [AnswerInput(101, 1001), AnswerInput(102, 1004)])
    assert res1.attempt.score == Decimal("33.33")
    assert res1.attempt.correct_count == 1

    # Retry submit with different (all-correct) answers -> still returns stored 33.33
    res2 = svc.submit(
        s1, a1.id, [AnswerInput(101, 1001), AnswerInput(102, 1003), AnswerInput(103, 1005)]
    )
    assert res2.attempt.score == Decimal("33.33")
    assert res2.attempt.correct_count == 1

    # Account isolation: s2 cannot read s1's attempt, teacher owner can
    with pytest.raises(DomainError) as exc_iso:
        svc.get_attempt(s2, a1.id)
    assert exc_iso.value.code == "FORBIDDEN"
    assert svc.get_attempt(_actor(10, "TEACHER"), a1.id).attempt.id == a1.id


def test_learning_api_routes():
    repo = FakeLearningRepo()
    svc = LearningService(repo, FakeUoW)
    app = create_app(Settings(_env_file=None, environment="test"))
    current = {"actor": _actor(20, "STUDENT")}
    app.dependency_overrides[current_actor] = lambda: current["actor"]
    app.dependency_overrides[get_learning_service] = lambda: svc

    with TestClient(app) as client:
        start = client.post("/api/v1/quizzes/1/attempts")
        assert start.status_code == 201
        aid = start.json()["id"]

        sub = client.post(
            f"/api/v1/attempts/{aid}/submit",
            json={"answers": [{"question_id": "101", "selected_option_id": "1001"}]},
        )
        assert sub.status_code == 200
        assert sub.json()["score"] == 33.33
        assert len(sub.json()["review"]) == 3

        hist = client.get("/api/v1/users/me/attempts")
        assert hist.status_code == 200
        assert hist.json()["total"] == 1
