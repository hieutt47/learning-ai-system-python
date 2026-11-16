"""Safe local thumbnail storage with magic-byte validation and path traversal protection."""

import os
import re
from pathlib import Path
from uuid import uuid4

from app.core.domain import DomainError

MAX_THUMBNAIL_BYTES = 5 * 1024 * 1024  # 5 MiB
_SAFE_KEY_RE = re.compile(r"^[0-9a-f]{32}\.(png|jpg|webp)$")
_EXT_MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "webp": "image/webp",
}


def detect_image_type(content: bytes) -> tuple[str, str] | None:
    """Return (extension, media_type) from magic bytes, or None if unsupported."""
    if len(content) >= 8 and content[:8] == b"\x89PNG\r\n\x1a\n":
        return ("png", "image/png")
    if len(content) >= 3 and content[:3] == b"\xff\xd8\xff":
        return ("jpg", "image/jpeg")
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ("webp", "image/webp")
    return None


class LocalThumbnailStorage:
    def __init__(self, root: Path | str | None = None):
        raw_root = root if root is not None else os.environ.get("APP_STORAGE_LOCATION", "./uploads")
        self.root = Path(raw_root).resolve()

    def resolve_key(self, storage_key: str) -> Path:
        if not _SAFE_KEY_RE.match(storage_key):
            raise DomainError("NOT_FOUND", 404, "File not found.")
        candidate = (self.root / storage_key).resolve()
        if not candidate.is_relative_to(self.root) or candidate.parent != self.root:
            raise DomainError("NOT_FOUND", 404, "File not found.")
        return candidate

    def store(self, content: bytes, ext: str) -> tuple[str, str]:
        if ext not in _EXT_MEDIA_TYPES:
            raise DomainError(
                "UNSUPPORTED_FILE_TYPE", 415, "Thumbnail must be a JPEG, PNG or WebP image."
            )
        self.root.mkdir(parents=True, exist_ok=True)
        storage_key = f"{uuid4().hex}.{ext}"
        target = self.resolve_key(storage_key)
        target.write_bytes(content)
        return storage_key, f"/api/v1/files/{storage_key}"

    def read(self, storage_key: str) -> tuple[bytes, str]:
        target = self.resolve_key(storage_key)
        if not target.is_file():
            raise DomainError("NOT_FOUND", 404, "File not found.")
        data = target.read_bytes()
        detected = detect_image_type(data)
        media_type = (
            detected[1]
            if detected is not None
            else _EXT_MEDIA_TYPES.get(target.suffix.lstrip("."), "application/octet-stream")
        )
        return data, media_type

    def delete_quietly(self, storage_key: str | None) -> None:
        if not storage_key or not _SAFE_KEY_RE.match(storage_key):
            return
        try:
            target = self.resolve_key(storage_key)
            target.unlink(missing_ok=True)
        except (OSError, DomainError):
            pass
