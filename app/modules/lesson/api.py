"""Actual Java/frontend compatible lesson routes; replacement PUT, raw list."""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.catalog_course_dependencies import SessionDependency, SqlUnitOfWork, WireId, invoke
from app.modules.identity.api.dependencies import current_actor
from app.modules.identity.domain.models import Actor
from app.modules.lesson.domain import LessonService
from app.modules.lesson.infrastructure import SqlCourseAccess, SqlLessonRepository

router = APIRouter(prefix="/api/v1", tags=["lessons"])


def get_lesson_service(session: SessionDependency) -> LessonService:
    return LessonService(
        SqlLessonRepository(session), SqlCourseAccess(session), lambda: SqlUnitOfWork(session)
    )


Service = Annotated[LessonService, Depends(get_lesson_service)]
CurrentActor = Annotated[Actor, Depends(current_actor)]


class LessonWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str
    video_url: str | None = Field(default=None, max_length=2048)
    transcript_text: str | None = None


class LessonResponse(BaseModel):
    id: str
    course_id: str
    title: str
    video_url: str
    transcript_text: str | None
    order_index: int
    document_count: int
    quiz_count: int
    flashcard_count: int
    can_delete: bool
    delete_blocked_reason: str | None
    created_at: datetime
    updated_at: datetime


def lesson_response(lesson) -> LessonResponse:
    values = asdict(lesson)
    values.pop("generation_count")
    values.update(id=str(lesson.id), course_id=str(lesson.course_id))
    return LessonResponse(**values)


@router.post("/courses/{course_id}/lessons", response_model=LessonResponse, status_code=201)
def create_lesson(
    course_id: WireId, body: LessonWrite, actor: CurrentActor, service: Service, response: Response
):
    lesson = invoke(service.create, actor, int(course_id), **body.model_dump())
    response.headers["Location"] = f"/api/v1/lessons/{lesson.id}"
    return lesson_response(lesson)


@router.get("/courses/{course_id}/lessons", response_model=list[LessonResponse])
def list_lessons(course_id: WireId, actor: CurrentActor, service: Service):
    return [lesson_response(item) for item in invoke(service.list, actor, int(course_id))]


@router.get("/lessons/{lesson_id}", response_model=LessonResponse)
def get_lesson(lesson_id: WireId, actor: CurrentActor, service: Service):
    return lesson_response(invoke(service.get, actor, int(lesson_id)))


@router.put("/lessons/{lesson_id}", response_model=LessonResponse)
def update_lesson(lesson_id: WireId, body: LessonWrite, actor: CurrentActor, service: Service):
    return lesson_response(invoke(service.update, actor, int(lesson_id), **body.model_dump()))


@router.delete("/lessons/{lesson_id}", status_code=204)
def delete_lesson(lesson_id: WireId, actor: CurrentActor, service: Service):
    invoke(service.delete, actor, int(lesson_id))
    return Response(status_code=204)
