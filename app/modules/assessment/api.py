"""Assessment FastAPI routes matching Java QuizController & QuizDraftSaveController."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.catalog_course_dependencies import SessionDependency, SqlUnitOfWork, WireId, invoke
from app.modules.assessment.domain import (
    AssessmentService,
    OptionInput,
    QuestionInput,
    QuestionRecord,
    QuizRecord,
)
from app.modules.assessment.infrastructure import SqlAssessmentRepository
from app.modules.identity.api.dependencies import current_actor
from app.modules.identity.domain.models import Actor

router = APIRouter(prefix="/api/v1", tags=["assessment"])


def get_assessment_service(session: SessionDependency) -> AssessmentService:
    return AssessmentService(SqlAssessmentRepository(session), lambda: SqlUnitOfWork(session))


Service = Annotated[AssessmentService, Depends(get_assessment_service)]
CurrentActor = Annotated[Actor, Depends(current_actor)]


class OptionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    content: str
    is_correct: bool
    order_index: int = Field(ge=0)


class QuestionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    content: str
    explanation: str | None = None
    order_index: int = Field(ge=0)
    options: list[OptionWrite]
    generation_id: WireId | None = None


class DraftQuestionItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: WireId | None = None
    content: str
    explanation: str | None = None
    order_index: int = Field(ge=0)
    options: list[OptionWrite]
    generation_id: WireId | None = None


class QuizCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str
    generation_id: WireId | None = None


class QuizUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str


class SaveQuizDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str
    title: str
    questions: list[DraftQuestionItem]

    @field_validator("request_id")
    @classmethod
    def valid_uuid(cls, value: str) -> str:
        parsed = UUID(value)
        return str(parsed).lower()


class QuizPublicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["PUBLISHED", "CLOSED"]


class OptionAuthorResponse(BaseModel):
    id: str
    content: str
    is_correct: bool
    order_index: int


class QuestionAuthorResponse(BaseModel):
    id: str
    content: str
    explanation: str | None
    order_index: int
    generated_by_ai: bool
    options: list[OptionAuthorResponse]


class QuizAuthorResponse(BaseModel):
    id: str
    lesson_id: str
    title: str
    status: str
    created_at: datetime
    questions: list[QuestionAuthorResponse]


class StudentOptionResponse(BaseModel):
    id: str
    content: str
    order_index: int


class StudentQuestionResponse(BaseModel):
    id: str
    content: str
    order_index: int
    options: list[StudentOptionResponse]


class QuizStudentResponse(BaseModel):
    id: str
    lesson_id: str
    title: str
    status: str
    questions: list[StudentQuestionResponse]


class QuizSummaryResponse(BaseModel):
    id: str
    lesson_id: str
    title: str
    status: str
    question_count: int


class QuizSummaryPage(BaseModel):
    items: list[QuizSummaryResponse]
    page: int
    page_size: int
    total: int


def _question_author_resp(q: QuestionRecord) -> QuestionAuthorResponse:
    return QuestionAuthorResponse(
        id=str(q.id),
        content=q.content,
        explanation=q.explanation,
        order_index=q.order_index,
        generated_by_ai=q.generated_by_ai,
        options=[
            OptionAuthorResponse(
                id=str(o.id),
                content=o.content,
                is_correct=o.is_correct,
                order_index=o.order_index,
            )
            for o in q.options
        ],
    )


def _quiz_author_resp(quiz: QuizRecord) -> QuizAuthorResponse:
    return QuizAuthorResponse(
        id=str(quiz.id),
        lesson_id=str(quiz.lesson_id),
        title=quiz.title,
        status=quiz.status,
        created_at=quiz.created_at,
        questions=[_question_author_resp(q) for q in quiz.questions],
    )


def _quiz_student_resp(quiz: QuizRecord) -> QuizStudentResponse:
    return QuizStudentResponse(
        id=str(quiz.id),
        lesson_id=str(quiz.lesson_id),
        title=quiz.title,
        status=quiz.status,
        questions=[
            StudentQuestionResponse(
                id=str(q.id),
                content=q.content,
                order_index=q.order_index,
                options=[
                    StudentOptionResponse(
                        id=str(o.id),
                        content=o.content,
                        order_index=o.order_index,
                    )
                    for o in q.options
                ],
            )
            for q in quiz.questions
        ],
    )


def _to_question_input(item: QuestionWrite | DraftQuestionItem) -> QuestionInput:
    qid = getattr(item, "id", None)
    return QuestionInput(
        id=int(qid) if qid is not None else None,
        content=item.content,
        explanation=item.explanation,
        order_index=item.order_index,
        generation_id=int(item.generation_id) if item.generation_id is not None else None,
        options=[OptionInput(o.content, o.is_correct, o.order_index) for o in item.options],
    )


@router.get("/lessons/{lesson_id}/quizzes", response_model=QuizSummaryPage)
def list_quizzes(
    lesson_id: WireId,
    actor: CurrentActor,
    service: Service,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    result = invoke(service.list_quizzes, actor, int(lesson_id), page, page_size)
    return QuizSummaryPage(
        items=[
            QuizSummaryResponse(
                id=str(item.id),
                lesson_id=str(item.lesson_id),
                title=item.title,
                status=item.status,
                question_count=item.question_count,
            )
            for item in result.items
        ],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.post("/lessons/{lesson_id}/quizzes", response_model=QuizAuthorResponse, status_code=201)
def create_quiz(
    lesson_id: WireId,
    body: QuizCreate,
    actor: CurrentActor,
    service: Service,
    response: Response,
):
    gen_id = int(body.generation_id) if body.generation_id is not None else None
    quiz = invoke(service.create_quiz, actor, int(lesson_id), body.title, gen_id)
    response.headers["Location"] = f"/api/v1/quizzes/{quiz.id}"
    return _quiz_author_resp(quiz)


@router.get("/quizzes/{quiz_id}", response_model=QuizAuthorResponse | QuizStudentResponse)
def get_quiz(quiz_id: WireId, actor: CurrentActor, service: Service):
    quiz, is_owner = invoke(service.get_quiz, actor, int(quiz_id))
    return _quiz_author_resp(quiz) if is_owner else _quiz_student_resp(quiz)


@router.patch("/quizzes/{quiz_id}", response_model=QuizAuthorResponse)
def update_quiz(quiz_id: WireId, body: QuizUpdate, actor: CurrentActor, service: Service):
    return _quiz_author_resp(invoke(service.update_quiz, actor, int(quiz_id), body.title))


@router.put("/quizzes/{quiz_id}/draft", response_model=QuizAuthorResponse)
def save_quiz_draft(
    quiz_id: WireId, body: SaveQuizDraftRequest, actor: CurrentActor, service: Service
):
    raw_json = invoke(
        service.save_draft,
        actor,
        int(quiz_id),
        body.request_id,
        body.title,
        [_to_question_input(q) for q in body.questions],
        lambda q: _quiz_author_resp(q).model_dump_json(),
    )
    return Response(content=raw_json, media_type="application/json")


@router.delete("/quizzes/{quiz_id}", status_code=204)
def delete_quiz(quiz_id: WireId, actor: CurrentActor, service: Service):
    invoke(service.delete_quiz, actor, int(quiz_id))
    return Response(status_code=204)


@router.post("/quizzes/{quiz_id}/questions", response_model=QuestionAuthorResponse, status_code=201)
def add_question(
    quiz_id: WireId,
    body: QuestionWrite,
    actor: CurrentActor,
    service: Service,
    response: Response,
):
    created = invoke(service.add_question, actor, int(quiz_id), _to_question_input(body))
    response.headers["Location"] = f"/api/v1/questions/{created.id}"
    return _question_author_resp(created)


@router.put("/questions/{question_id}", response_model=QuestionAuthorResponse)
def replace_question(
    question_id: WireId, body: QuestionWrite, actor: CurrentActor, service: Service
):
    return _question_author_resp(
        invoke(service.replace_question, actor, int(question_id), _to_question_input(body))
    )


@router.delete("/questions/{question_id}", status_code=204)
def delete_question(question_id: WireId, actor: CurrentActor, service: Service):
    invoke(service.delete_question, actor, int(question_id))
    return Response(status_code=204)


@router.patch("/quizzes/{quiz_id}/publication", response_model=QuizAuthorResponse)
def change_publication(
    quiz_id: WireId,
    body: QuizPublicationRequest,
    actor: CurrentActor,
    service: Service,
):
    return _quiz_author_resp(invoke(service.change_publication, actor, int(quiz_id), body.status))
