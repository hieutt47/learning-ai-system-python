from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.domain import DomainError, ResourceInUse
from app.modules.catalog.domain import (
    CatalogService,
    Category,
    DuplicateName,
)
from app.modules.course.domain import Course, CourseService

ADMIN = SimpleNamespace(id=1, role="ADMIN")
TEACHER = SimpleNamespace(id=2, role="TEACHER")
OTHER = SimpleNamespace(id=3, role="TEACHER")
STUDENT = SimpleNamespace(id=4, role="STUDENT")


class Categories:
    def __init__(self):
        self.rows = {1: Category(1, "Science", "Description")}
        self.used = False
        self.failure = None
        self.locks = []

    def list(self, page, page_size):
        rows = sorted(self.rows.values(), key=lambda row: row.id, reverse=True)
        return rows[(page - 1) * page_size : page * page_size], len(rows)

    def get(self, category_id, *, for_update=False):
        self.locks.append(for_update)
        return self.rows.get(category_id)

    def exists(self, category_id, *, for_update=False):
        return self.get(category_id, for_update=for_update) is not None

    def name_exists(self, name, *, excluding_id=None):
        return any(
            row.name.casefold() == name.casefold() and row.id != excluding_id
            for row in self.rows.values()
        )

    def save(self, category):
        if self.failure:
            raise self.failure
        row = replace(category, id=category.id or max(self.rows, default=0) + 1)
        self.rows[row.id] = row
        return row

    def has_courses(self, category_id):
        return self.used

    def delete(self, category_id):
        if self.failure:
            raise self.failure
        del self.rows[category_id]


class Courses:
    def __init__(self):
        self.rows = {1: Course(1, "Title", "Description", 1, 2, Decimal("0"))}
        self.links = ()
        self.failure = None
        self.locks = []

    def list(self, page, page_size, *, teacher_id):
        rows = sorted(
            (
                row
                for row in self.rows.values()
                if teacher_id is None or row.teacher_id == teacher_id
            ),
            key=lambda row: row.id,
            reverse=True,
        )
        return rows[(page - 1) * page_size : page * page_size], len(rows)

    def get(self, course_id, *, for_update=False):
        self.locks.append(for_update)
        return self.rows.get(course_id)

    def save(self, course):
        if self.failure:
            raise self.failure
        row = replace(course, id=course.id or max(self.rows, default=0) + 1)
        self.rows[row.id] = row
        return row

    def dependencies(self, course_id):
        return self.links

    def delete(self, course_id):
        if self.failure:
            raise self.failure
        del self.rows[course_id]


class Transaction:
    def __init__(self, categories, courses):
        self.categories, self.courses = categories, courses
        self.commits = 0
        self.failure = None

    @contextmanager
    def __call__(self):
        categories, courses = dict(self.categories.rows), dict(self.courses.rows)
        try:
            yield self
        except Exception:
            self.categories.rows, self.courses.rows = categories, courses
            raise

    def commit(self):
        if self.failure:
            raise self.failure
        self.commits += 1


@pytest.fixture
def services():
    categories, courses = Categories(), Courses()
    tx = Transaction(categories, courses)
    return SimpleNamespace(
        categories=categories,
        courses=courses,
        tx=tx,
        catalog=CatalogService(categories, tx),
        course=CourseService(courses, categories, tx),
    )


def error(code, status):
    return pytest.raises(DomainError, check=lambda exc: exc.code == code and exc.status == status)


def test_catalog_public_pages_and_defaults(services):
    page = services.catalog.list()
    assert (page.items, page.page, page.page_size, page.total) == (
        [services.categories.rows[1]],
        1,
        20,
        1,
    )
    services.catalog.create(ADMIN, "Another")
    assert services.catalog.list(2, 1).items[0].id == 1


@pytest.mark.parametrize("page,size", [(0, 20), (1, 0), (1, 101), (True, 20), (1, 1.5)])
def test_invalid_pagination(services, page, size):
    with error("VALIDATION_ERROR", 400):
        services.catalog.list(page, size)
    with error("VALIDATION_ERROR", 400):
        services.course.list(ADMIN, page, size)


@pytest.mark.parametrize("actor", [TEACHER, STUDENT])
def test_catalog_admin_only_writes(services, actor):
    for operation in (
        lambda: services.catalog.create(actor, "New"),
        lambda: services.catalog.update(actor, 1, name="New"),
        lambda: services.catalog.delete(actor, 1),
    ):
        with error("FORBIDDEN", 403):
            operation()
    assert services.tx.commits == 0


def test_category_create_update_trim_and_null_noop(services):
    category = services.catalog.create(ADMIN, "  New  ", "Optional")
    assert category.name == "New" and category.description == "Optional"
    assert services.catalog.update(ADMIN, category.id, name=None, description=None) == category
    updated = services.catalog.update(ADMIN, category.id, name=" Renamed ", description="")
    assert (updated.name, updated.description) == ("Renamed", "")
    assert services.categories.locks[-1]


@pytest.mark.parametrize("name", ["", " \t ", "x" * 121])
def test_category_invalid_names(services, name):
    with error("VALIDATION_ERROR", 400):
        services.catalog.create(ADMIN, name)
    with error("VALIDATION_ERROR", 400):
        services.catalog.update(ADMIN, 1, name=name)


def test_category_duplicate_precheck_and_adapter_race(services):
    with error("INVALID_STATE", 409):
        services.catalog.create(ADMIN, " science ")
    services.categories.failure = DuplicateName()
    with error("INVALID_STATE", 409):
        services.catalog.update(ADMIN, 1, name="Changed")
    with error("INVALID_STATE", 409):
        services.catalog.create(ADMIN, "New")


def test_category_commit_conflict_rolls_back(services):
    services.tx.failure = DuplicateName()
    with error("INVALID_STATE", 409):
        services.catalog.create(ADMIN, "New")
    assert list(services.categories.rows) == [1]


def test_category_missing_update_delete(services):
    for operation in (
        lambda: services.catalog.update(ADMIN, 999),
        lambda: services.catalog.delete(ADMIN, 999),
    ):
        with error("NOT_FOUND", 404):
            operation()


@pytest.mark.parametrize("race", [False, True])
def test_category_in_use_delete(services, race):
    services.categories.used = not race
    services.categories.failure = ResourceInUse() if race else None
    with error("RESOURCE_IN_USE", 409):
        services.catalog.delete(ADMIN, 1)
    assert 1 in services.categories.rows and services.tx.commits == 0


def test_category_unused_delete(services):
    services.catalog.delete(ADMIN, 1)
    assert services.categories.rows == {} and services.tx.commits == 1
    assert services.categories.locks[-1]


@pytest.mark.parametrize("actor", [TEACHER, ADMIN])
def test_course_create_actor_owns_draft(services, actor):
    course = services.course.create(
        actor,
        title=" Title ",
        description=" Description ",
        category_id=1,
        price=Decimal("999999999999.99"),
    )
    assert (course.teacher_id, course.title, course.description) == (
        actor.id,
        "Title",
        "Description",
    )
    assert not course.is_published and course.archived_at is None
    assert services.categories.locks[-1] and services.tx.commits == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", " "),
        ("title", "x" * 201),
        ("description", "\t"),
        ("price", Decimal("-1")),
        ("price", Decimal("1000000000000")),
        ("price", Decimal("0.001")),
        ("price", Decimal("NaN")),
        ("price", Decimal("Infinity")),
        ("price", 1.5),
    ],
)
def test_course_invalid_create_and_patch(services, field, value):
    kwargs = dict(title="Title", description="Description", category_id=1, price=Decimal("0"))
    kwargs[field] = value
    with error("VALIDATION_ERROR", 400):
        services.course.create(TEACHER, **kwargs)
    with error("VALIDATION_ERROR", 400):
        services.course.update(TEACHER, 1, **{field: value})
    assert services.tx.commits == 0


def test_course_missing_categories_create_patch(services):
    with error("NOT_FOUND", 404):
        services.course.create(
            TEACHER, title="Title", description="Description", category_id=999, price=Decimal("0")
        )
    with error("NOT_FOUND", 404):
        services.course.update(TEACHER, 1, category_id=999)
    assert len(services.courses.rows) == 1 and services.tx.commits == 0


def test_student_denied_all_course_operations(services):
    operations = [
        lambda: services.course.list(STUDENT),
        lambda: services.course.get(STUDENT, 1),
        lambda: services.course.create(
            STUDENT, title="Title", description="Description", category_id=1, price=Decimal("0")
        ),
        lambda: services.course.update(STUDENT, 1),
        lambda: services.course.delete(STUDENT, 1),
    ]
    for operation in operations:
        with error("FORBIDDEN", 403):
            operation()


def test_course_owner_admin_and_nonowner_get_update_delete(services):
    assert services.course.get(TEACHER, 1) == services.course.get(ADMIN, 1)
    for operation in (
        lambda: services.course.get(OTHER, 1),
        lambda: services.course.update(OTHER, 1),
        lambda: services.course.delete(OTHER, 1),
    ):
        with error("FORBIDDEN", 403):
            operation()
    assert services.course.update(ADMIN, 1, title="Admin edit").teacher_id == TEACHER.id


def test_course_missing_get_update_delete(services):
    for operation in (
        lambda: services.course.get(TEACHER, 999),
        lambda: services.course.update(TEACHER, 999),
        lambda: services.course.delete(TEACHER, 999),
    ):
        with error("NOT_FOUND", 404):
            operation()


def test_course_list_owner_filter_admin_all_and_stable_pages(services):
    services.courses.rows[2] = replace(services.courses.rows[1], id=2, teacher_id=3)
    assert services.course.list(TEACHER).total == 1
    assert services.course.list(OTHER).items[0].id == 2
    assert services.course.list(ADMIN).total == 2
    assert services.course.list(ADMIN, 2, 1).items[0].id == 1


def test_course_patch_null_noop_and_valid_changes(services):
    original = services.courses.rows[1]
    assert (
        services.course.update(
            TEACHER, 1, title=None, description=None, category_id=None, price=None
        )
        == original
    )
    services.categories.rows[2] = Category(2, "Other")
    updated = services.course.update(
        TEACHER, 1, title=" New ", description=" Updated ", category_id=2, price=Decimal("12.34")
    )
    assert (updated.title, updated.description, updated.category_id, updated.price) == (
        "New",
        "Updated",
        2,
        Decimal("12.34"),
    )
    assert services.courses.locks[-1]


@pytest.mark.parametrize("state", [dict(is_published=True), dict(archived_at=datetime.now(UTC))])
def test_course_nondraft_write_scope_limit(services, state):
    services.courses.rows[1] = replace(services.courses.rows[1], **state)
    for operation in (
        lambda: services.course.update(ADMIN, 1),
        lambda: services.course.delete(ADMIN, 1),
    ):
        with error("INVALID_STATE", 409):
            operation()
    assert services.tx.commits == 0


@pytest.mark.parametrize("links", [("lessons",), ("memberships",), ("ai_generation",)])
def test_course_dependency_guard(services, links):
    services.courses.links = links
    with error("INVALID_STATE", 409):
        services.course.delete(TEACHER, 1)
    assert 1 in services.courses.rows and services.tx.commits == 0


def test_course_delete_race_and_success(services):
    services.courses.failure = ResourceInUse()
    with error("INVALID_STATE", 409):
        services.course.delete(TEACHER, 1)
    services.courses.failure = None
    services.course.delete(TEACHER, 1)
    assert services.courses.rows == {} and services.tx.commits == 1
    assert services.courses.locks[-1]


def test_course_save_failure_rolls_back(services):
    services.tx.failure = RuntimeError("commit failed")
    with pytest.raises(RuntimeError, match="commit failed"):
        services.course.update(TEACHER, 1, title="Changed")
    assert services.courses.rows[1].title == "Title"
