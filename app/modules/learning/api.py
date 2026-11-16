"""Learning FastAPI routes matching Java AttemptController."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict

from app.core.catalog_course_dependencies import SessionDependency, SqlUnitOfWork, WireId, invoke
from app.modules.identity.api.dependencies import current_actor
from app.modules.identity.domain.models import Actor
from app.modules.learning.domain import (
    AnswerInput,
    AttemptRecord,
    AttemptResult,
    LearningService,
)
from app.modules.learning.infrastructure import SqlLearningRepository

router = APIRouter(prefix="/api/v1", tags=["learning"])


def get_learning_service(session: SessionDependency) -> LearningService:
    return LearningService(SqlLearningRepository(session), lambda: SqlUnitOfWork(session))


Service = Annotated[LearningService, Depends(get_learning_service)]
CurrentActor = Annotated[Actor, Depends(current_actor)]


class SubmitAnswerItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question_id: WireId
    selected_option_id: WireId | None = None


class SubmitAttemptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answers: list[SubmitAnswerItem]


class AttemptResponse(BaseModel):
    id: str
    quiz_id: str
    student_id: str
    status: str
    total_questions: int
    started_at: datetime


class ReviewOptionResponse(BaseModel):
    id: str
    content: str


class ReviewItemResponse(BaseModel):
    question_id: str
    question_content: str
    selected_option_id: str | None
    is_correct: bool
    correct_option_id: str
    explanation: str | None
    options: list[ReviewOptionResponse]


class AttemptResultResponse(BaseModel):
    id: str
    quiz_id: str
    quiz_title: str
    student_id: str
    status: str
    score: float
    total_questions: int
    correct_count: int
    started_at: datetime
    submitted_at: datetime
    review: list[ReviewItemResponse]


class AttemptSummaryResponse(BaseModel):
    id: str
    quiz_id: str
    quiz_title: str
    course_id: str
    course_title: str
    status: str
    score: float | None
    started_at: datetime
    submitted_at: datetime | None


class AttemptSummaryPage(BaseModel):
    items: list[AttemptSummaryResponse]
    page: int
    page_size: int
    total: int


def _attempt_response(a: AttemptRecord) -> AttemptResponse:
    return AttemptResponse(
        id=str(a.id),
        quiz_id=str(a.quiz_id),
        student_id=str(a.student_id),
        status=a.status,
        total_questions=a.total_questions,
        started_at=a.started_at,
    )


def _result_response(res: AttemptResult) -> AttemptResultResponse:
    a = res.attempt
    return AttemptResultResponse(
        id=str(a.id),
        quiz_id=str(a.quiz_id),
        quiz_title=a.quiz_title,
        student_id=str(a.student_id),
        status=a.status,
        score=float(a.score) if a.score is not None else 0.0,
        total_questions=a.total_questions,
        correct_count=a.correct_count or 0,
        started_at=a.started_at,
        submitted_at=a.submitted_at,
        review=[
            ReviewItemResponse(
                question_id=str(r.question_id),
                question_content=r.question_content,
                selected_option_id=(
                    str(r.selected_option_id) if r.selected_option_id is not None else None
                ),
                is_correct=r.is_correct,
                correct_option_id=str(r.correct_option_id),
                explanation=r.explanation,
                options=[ReviewOptionResponse(id=str(oid), content=oc) for oid, oc in r.options],
            )
            for r in res.review
        ],
    )


@router.get("/users/me/attempts", response_model=AttemptSummaryPage)
def attempt_history(
    actor: CurrentActor,
    service: Service,
    quiz_id: WireId | None = None,
    course_id: WireId | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    result = invoke(
        service.history,
        actor,
        quiz_id=int(quiz_id) if quiz_id is not None else None,
        course_id=int(course_id) if course_id is not None else None,
        page=page,
        page_size=page_size,
    )
    return AttemptSummaryPage(
        items=[
            AttemptSummaryResponse(
                id=str(item.id),
                quiz_id=str(item.quiz_id),
                quiz_title=item.quiz_title,
                course_id=str(item.course_id),
                course_title=item.course_title,
                status=item.status,
                score=float(item.score) if item.score is not None else None,
                started_at=item.started_at,
                submitted_at=item.submitted_at,
            )
            for item in result.items
        ],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.post("/quizzes/{quiz_id}/attempts", response_model=AttemptResponse)
def start_attempt(
    quiz_id: WireId,
    actor: CurrentActor,
    service: Service,
    response: Response,
):
    attempt, created = invoke(service.start, actor, int(quiz_id))
    response.status_code = 201 if created else 200
    if created:
        response.headers["Location"] = f"/api/v1/attempts/{attempt.id}"
    return _attempt_response(attempt)


@router.post("/attempts/{attempt_id}/submit", response_model=AttemptResultResponse)
def submit_attempt(
    attempt_id: WireId,
    body: SubmitAttemptRequest,
    actor: CurrentActor,
    service: Service,
):
    answers = [
        AnswerInput(
            question_id=int(item.question_id),
            selected_option_id=(
                int(item.selected_option_id) if item.selected_option_id is not None else None
            ),
        )
        for item in body.answers
    ]
    return _result_response(invoke(service.submit, actor, int(attempt_id), answers))


@router.get("/attempts/{attempt_id}", response_model=AttemptResponse | AttemptResultResponse)
def get_attempt(attempt_id: WireId, actor: CurrentActor, service: Service):
    res = invoke(service.get_attempt, actor, int(attempt_id))
    if isinstance(res, AttemptRecord):
        return _attempt_response(res)
    return _result_response(res)
