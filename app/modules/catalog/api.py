"""Public category listing and administrator category management."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict

from app.core.catalog_course_dependencies import SessionDependency, SqlUnitOfWork, WireId, invoke
from app.modules.catalog.domain import CatalogService, DuplicateName
from app.modules.catalog.infrastructure import SqlCategoryRepository
from app.modules.identity.api.dependencies import current_actor
from app.modules.identity.domain.models import Actor

router = APIRouter(prefix="/api/v1", tags=["categories"])


def get_catalog_service(session: SessionDependency) -> CatalogService:
    return CatalogService(
        SqlCategoryRepository(session),
        lambda: SqlUnitOfWork(session, duplicate_error=DuplicateName),
    )


Service = Annotated[CatalogService, Depends(get_catalog_service)]
CurrentActor = Annotated[Actor, Depends(current_actor)]


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    description: str | None = None


class CategoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str | None = None
    description: str | None = None


class CategoryResponse(BaseModel):
    id: str
    name: str
    description: str | None


class CategoryPage(BaseModel):
    items: list[CategoryResponse]
    page: int
    page_size: int
    total: int


def category_response(category) -> CategoryResponse:
    return CategoryResponse(
        id=str(category.id), name=category.name, description=category.description
    )


@router.get("/categories", response_model=CategoryPage)
def list_categories(
    service: Service,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    result = invoke(service.list, page, page_size)
    return CategoryPage(
        items=[category_response(item) for item in result.items],
        page=result.page,
        page_size=result.page_size,
        total=result.total,
    )


@router.post("/categories", response_model=CategoryResponse, status_code=201)
def create_category(body: CategoryCreate, actor: CurrentActor, service: Service):
    return category_response(invoke(service.create, actor, **body.model_dump()))


@router.patch("/categories/{category_id}", response_model=CategoryResponse)
def patch_category(category_id: WireId, body: CategoryPatch, actor: CurrentActor, service: Service):
    return category_response(invoke(service.update, actor, int(category_id), **body.model_dump()))


@router.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: WireId, actor: CurrentActor, service: Service):
    invoke(service.delete, actor, int(category_id))
    return Response(status_code=204)
