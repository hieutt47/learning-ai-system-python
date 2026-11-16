"""Enrollment routes: enroll, my courses, and course members."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from app.core.catalog_course_dependencies import SessionDependency, SqlUnitOfWork, WireId, invoke
from app.modules.enrollment.domain import EnrollmentService, Membership
from app.modules.enrollment.infrastructure import SqlEnrollments
from app.modules.identity.api.dependencies import current_actor
from app.modules.identity.domain.models import Actor

router = APIRouter(prefix="/api/v1", tags=["enrollment"])


def get_enrollment_service(session: SessionDependency) -> EnrollmentService:
    return EnrollmentService(SqlEnrollments(session), lambda: SqlUnitOfWork(session))


Service = Annotated[EnrollmentService, Depends(get_enrollment_service)]
CurrentActor = Annotated[Actor, Depends(current_actor)]


class EnrollmentResponse(BaseModel):
    course_id: str
    student_id: str
    enrolled_at: datetime


class CategorySummary(BaseModel):
    id: str
    name: str


class TeacherSummary(BaseModel):
    id: str
    full_name: str


class CourseEnrollmentResponse(BaseModel):
    id: str
    title: str
    description: str
    category: CategorySummary
    teacher: TeacherSummary
    thumbnail_url: str | None
    price: str
    currency: str
    total_students: int
    lesson_count: int
    outline: list[dict] | None
    archived_at: datetime | None
    enrolled_at: datetime


class CourseEnrollmentPage(BaseModel):
    items: list[CourseEnrollmentResponse]
    page: int
    page_size: int
    total: int


class MemberResponse(BaseModel):
    course_id: str
    student_id: str
    full_name: str
    enrolled_at: datetime


class MemberPage(BaseModel):
    items: list[MemberResponse]
    page: int
    page_size: int
    total: int


def _enrollment_response(m: Membership) -> EnrollmentResponse:
    return EnrollmentResponse(
        course_id=str(m.course_id),
        student_id=str(m.student_id),
        enrolled_at=m.enrolled_at,
    )


@router.post("/courses/{course_id}/enroll", response_model=EnrollmentResponse)
def enroll(
    course_id: WireId,
    actor: CurrentActor,
    service: Service,
    response: Response,
):
    membership, created = invoke(service.enroll, actor, int(course_id))
    response.status_code = 201 if created else 200
    return _enrollment_response(membership)


@router.get("/users/me/courses", response_model=CourseEnrollmentPage)
def my_courses(
    actor: CurrentActor,
    service: Service,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    result = invoke(service.mine, actor, page, page_size)
    return CourseEnrollmentPage(
        items=[CourseEnrollmentResponse(**item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/courses/{course_id}/members", response_model=MemberPage)
def course_members(
    course_id: WireId,
    actor: CurrentActor,
    service: Service,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    result = invoke(service.members, actor, int(course_id), page, page_size)
    return MemberPage(
        items=[MemberResponse(**item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )
