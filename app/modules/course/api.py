"""Owner/administrator draft course CRUD; marketplace/publication are out of scope."""

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.catalog_course_dependencies import SessionDependency, SqlUnitOfWork, WireId, invoke
from app.modules.course.domain import CourseService
from app.modules.course.infrastructure import SqlCourseCategoryLookup, SqlCourseRepository
from app.modules.identity.api.dependencies import current_actor
from app.modules.identity.domain.models import Actor

router = APIRouter(prefix="/api/v1", tags=["courses"])
Price = Annotated[str, Field(strict=True, pattern=r"^[0-9]{1,12}(\.[0-9]{1,2})?$")]


def get_course_service(session: SessionDependency) -> CourseService:
    return CourseService(
        SqlCourseRepository(session),
        SqlCourseCategoryLookup(session),
        lambda: SqlUnitOfWork(session),
    )


Service = Annotated[CourseService, Depends(get_course_service)]
CurrentActor = Annotated[Actor, Depends(current_actor)]


class CourseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str
    description: str
    category_id: WireId
    price: Price


class CoursePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str | None = None
    description: str | None = None
    category_id: WireId | None = None
    price: Price | None = None


class CourseResponse(BaseModel):
    id: str
    title: str
    description: str
    category_id: str
    teacher_id: str
    price: str
    is_published: bool
    archived_at: datetime | None
    thumbnail_url: str | None
    created_at: datetime
    updated_at: datetime
    category_name: str
    teacher_name: str
    lesson_count: int
    total_students: int


class CoursePage(BaseModel):
    items: list[CourseResponse]
    page: int
    page_size: int
    total: int


def course_response(course) -> CourseResponse:
    values = asdict(course)
    for field in ("id", "category_id", "teacher_id"):
        values[field] = str(values[field])
    values["price"] = format(course.price, ".2f")
    return CourseResponse(**values)


def changes(body) -> dict:
    values = body.model_dump()
    if values.get("price") is not None:
        values["price"] = Decimal(values["price"])
    if values.get("category_id") is not None:
        values["category_id"] = int(values["category_id"])
    return values


@router.post("/courses", response_model=CourseResponse, status_code=201)
def create_course(body: CourseCreate, actor: CurrentActor, service: Service, response: Response):
    course = invoke(service.create, actor, **changes(body))
    response.headers["Location"] = f"/api/v1/teaching/courses/{course.id}"
    return course_response(course)


@router.get("/teaching/courses", response_model=CoursePage)
def list_courses(
    actor: CurrentActor,
    service: Service,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    result = invoke(service.list, actor, page, page_size)
    return CoursePage(
        items=[course_response(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/teaching/courses/{course_id}", response_model=CourseResponse)
def get_course(course_id: WireId, actor: CurrentActor, service: Service):
    return course_response(invoke(service.get, actor, int(course_id)))


@router.patch("/courses/{course_id}", response_model=CourseResponse)
def patch_course(course_id: WireId, body: CoursePatch, actor: CurrentActor, service: Service):
    return course_response(invoke(service.update, actor, int(course_id), **changes(body)))


@router.delete("/courses/{course_id}", status_code=204)
def delete_course(course_id: WireId, actor: CurrentActor, service: Service):
    invoke(service.delete, actor, int(course_id))
    return Response(status_code=204)
