"""Owner/administrator draft course CRUD; marketplace/publication are out of scope."""

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from app.core.api_errors import ApiError
from app.core.catalog_course_dependencies import SessionDependency, SqlUnitOfWork, WireId, invoke
from app.modules.course.domain import CourseService, MarketplaceService
from app.modules.course.infrastructure import SqlCourseCategoryLookup, SqlCourseRepository
from app.modules.course.storage import MAX_THUMBNAIL_BYTES, LocalThumbnailStorage, detect_image_type
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


def get_marketplace_service(session: SessionDependency) -> MarketplaceService:
    return MarketplaceService(SqlCourseRepository(session), SqlCourseCategoryLookup(session))


Service = Annotated[CourseService, Depends(get_course_service)]
MarketplaceSvc = Annotated[MarketplaceService, Depends(get_marketplace_service)]
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


class CoursePublicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    is_published: bool


class CourseArchiveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    archived: bool


class PublicCategorySummary(BaseModel):
    id: str
    name: str


class PublicTeacherSummary(BaseModel):
    id: str
    full_name: str


class OutlineItemResponse(BaseModel):
    id: str
    title: str
    order_index: int


class CoursePublicResponse(BaseModel):
    id: str
    title: str
    description: str
    category: PublicCategorySummary
    teacher: PublicTeacherSummary
    thumbnail_url: str | None
    price: str
    currency: str
    total_students: int
    lesson_count: int
    outline: list[OutlineItemResponse] | None = None


class CoursePublicPage(BaseModel):
    items: list[CoursePublicResponse]
    page: int
    page_size: int
    total: int


def course_response(course) -> CourseResponse:
    values = asdict(course)
    values.pop("thumbnail_storage_key", None)
    values.pop("usable_lesson_count", None)
    for field in ("id", "category_id", "teacher_id"):
        values[field] = str(values[field])
    values["price"] = format(course.price, ".2f")
    return CourseResponse(**values)


def get_thumbnail_storage() -> LocalThumbnailStorage:
    return LocalThumbnailStorage()


ThumbnailStorage = Annotated[LocalThumbnailStorage, Depends(get_thumbnail_storage)]


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


@router.post("/courses/{course_id}/thumbnail", response_model=CourseResponse)
async def upload_thumbnail(
    course_id: WireId,
    actor: CurrentActor,
    service: Service,
    storage: ThumbnailStorage,
    file: Annotated[UploadFile, File()],
):
    content = await file.read(MAX_THUMBNAIL_BYTES + 1)
    if not content:
        raise ApiError(400, "VALIDATION_ERROR", "Thumbnail file is required.")
    if len(content) > MAX_THUMBNAIL_BYTES:
        raise ApiError(413, "FILE_TOO_LARGE", "Thumbnail must be at most 5 MiB.")
    detected = detect_image_type(content)
    if detected is None:
        raise ApiError(415, "UNSUPPORTED_FILE_TYPE", "Thumbnail must be a JPEG, PNG or WebP image.")
    ext, _ = detected
    storage_key, thumbnail_url = storage.store(content, ext)
    try:
        updated, old_key = invoke(
            service.update_thumbnail, actor, int(course_id), storage_key, thumbnail_url
        )
    except Exception:
        storage.delete_quietly(storage_key)
        raise
    if old_key and old_key != storage_key:
        storage.delete_quietly(old_key)
    return course_response(updated)


@router.get("/files/{storage_key}")
def get_file(storage_key: str, storage: ThumbnailStorage):
    data, media_type = invoke(storage.read, storage_key)
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=2592000",
        },
    )


@router.patch("/courses/{course_id}/publication", response_model=CourseResponse)
def set_publication(
    course_id: WireId, body: CoursePublicationRequest, actor: CurrentActor, service: Service
):
    return course_response(invoke(service.set_published, actor, int(course_id), body.is_published))


@router.patch("/courses/{course_id}/archive", response_model=CourseResponse)
def archive_course(
    course_id: WireId, body: CourseArchiveRequest, actor: CurrentActor, service: Service
):
    if body.archived is not True:
        raise ApiError(400, "VALIDATION_ERROR", "Only archived=true is supported.")
    return course_response(invoke(service.archive, actor, int(course_id)))


@router.get("/courses", response_model=CoursePublicPage)
def search_marketplace(
    service: MarketplaceSvc,
    q: str | None = None,
    category_id: WireId | None = None,
    sort: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    cat = int(category_id) if category_id is not None else None
    result = invoke(service.search, q=q, category_id=cat, sort=sort, page=page, page_size=page_size)
    return CoursePublicPage(
        items=[CoursePublicResponse(**item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.get("/courses/{course_id}", response_model=CoursePublicResponse)
def get_public_course(course_id: WireId, service: MarketplaceSvc):
    return CoursePublicResponse(**invoke(service.get_public, int(course_id)))
