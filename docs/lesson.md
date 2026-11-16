# TASK008 lesson CRUD and authorized read

Reuses the actual Java baseline `51a88ce` read-only: `LessonController`,
`LessonService`, `LessonWriteRequest`, `LessonResponse`, lesson entity and V1–V9
schema; verified the frontend `features/teaching/lessons/lesson.api.ts` consumer.
No schema/migration changes or Java writes.

## Compatibility and scope

- `POST /api/v1/courses/{course_id}/lessons` returns 201, lesson object and Location.
- `GET /api/v1/courses/{course_id}/lessons` returns an ordered raw lesson array.
- `GET /api/v1/lessons/{lesson_id}` returns one authorized lesson.
- `PUT /api/v1/lessons/{lesson_id}` replaces content; title is required. Omitted or
  null video clears to `""`; omitted, null or blank transcript clears to null.
- `DELETE /api/v1/lessons/{lesson_id}` returns 204 for an unused draft only.

PUT and raw arrays deliberately match the actual Java/frontend implementation;
older Markdown describes PATCH and a page. No silent PATCH alias or mixed
semantics. Reorder, public outline, documents/uploads/downloads/text extraction,
AI, publication and enrollment mutation are outside TASK008.

Active sessions come from existing `current_actor`. Only owner TEACHER or ADMIN
can mutate; mutations currently require a nonpublished, nonarchived draft course.
This conservative partial scope avoids changing published validity before a
published-edit policy is implemented. An enrolled STUDENT can read lesson content
also after archive/unpublish, but `transcript_text` is always null. Other teachers
are forbidden. No public private-content access. Internal generation counts are
not serialized.

Wire IDs are positive signed BIGINT strings; JSON extras are rejected. Responses
include the Java/frontend counts and delete indicators plus UTC timestamps.
Title is trimmed, nonblank, at most 200 characters. Optional video must be an
HTTP(S) YouTube watch, youtu.be, embed or shorts URL with an 11-character video ID;
iframe HTML, arbitrary hosts, credentials and malformed IDs are rejected. Raw
video IDs are not accepted by this URL-only frontend field. Empty video is valid
for draft creation; publication must separately require publishable lessons.
Transcript is trimmed and bounded to the reused MySQL TEXT UTF-8 byte capacity.

## Persistence and deletion safety

Local repository and narrow course-access protocols keep the domain independent
of HTTP/ORM. The SQL course-access adapter does not call another module's
repository. Request-scoped existing Session/UoW/error mapping are reused.

Every mutation locks the parent course. Creation gets the greatest current order
under that lock, then appends at MAX+1, including empty courses. Existing unique
(course_id, order_index) remains authoritative. Deleting leaves stable gaps;
this differs from Java's compaction and is intentional until reorder is scoped.
No caller-provided order_index is accepted. Memberships, documents, quizzes,
flashcards and AI-generation provenance block deletion with safe 409; no cascade
or file cleanup. Current locking dependency reads avoid stale snapshot deletion;
restrictive FK races also map to 409. Existing quiz histories are protected
through their quiz dependency. A used/unpublished draft with memberships cannot
be deleted.

## Verification

Run the three owned suites with the project Python environment:

```text
python -m pytest tests/test_lesson_domain.py tests/test_lesson_api.py tests/test_lesson_persistence.py -q
```

Tests compose an independent FastAPI router and real lesson domain service using
fake ports. They cover owner/admin, wrong teacher, missing enrollment, student
transcript redaction, identity boundary, replacement/null behavior, strict bodies,
BIGINT bounds, YouTube validation, draft gates, dependency/restrict races and
UTC/ID/Location wire fields. Persistence tests inspect parameter binding/current
lock SQL and compile with the MySQL dialect. They are not real-database lock,
race, transaction-isolation or vertical-slice integration evidence; no MySQL was
started. App wiring and canonical contract integration belong to the coordinator.
