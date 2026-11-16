from datetime import UTC, datetime, timedelta, timezone

import bcrypt
import jwt
import pytest
from pydantic import ValidationError

from app.modules.identity.domain.models import Actor, DomainError
from app.modules.identity.infrastructure.crypto import BCryptVerifier, JwtCodec
from app.modules.identity.infrastructure.persistence import UtcDateTime
from app.modules.identity.infrastructure.settings import AuthSettings

SECRET = "unit-test-only-key-32-bytes-minimum-not-for-deployment"


def test_bcrypt_compatibility_and_no_silent_truncation():
    verifier = BCryptVerifier()
    for prefix in [b"2a", b"2b"]:
        encoded = bcrypt.hashpw(b" correct ", bcrypt.gensalt(rounds=10, prefix=prefix)).decode()
        assert verifier.verify(" correct ", encoded)
        assert not verifier.verify("correct", encoded)
        assert not verifier.verify("wrong", encoded)
    assert not verifier.verify("dummy-password-never-accepted", None)
    assert not verifier.verify("x" * 73, "$2b$10$invalid")
    assert not verifier.verify("x", "invalid")
    assert not verifier.verify("x", "non-ascii-é")


def test_jwt_claims_and_expiry():
    now = datetime.now(UTC)
    codec = JwtCodec(SECRET)
    actor = Actor(1, "Demo", "demo@example.test", "TEACHER", True, now)
    token = codec.issue(actor, now)
    claims = jwt.decode(token, SECRET, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 900
    assert claims["role"] == ["ROLE_TEACHER"]
    assert codec.subject(token) == actor.email
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


@pytest.mark.parametrize(
    "changes",
    [
        {"exp": None},
        {"iat": None},
        {"sub": None},
        {"sub": 1},
        {"sub": ""},
        {"exp": "9999999999"},
        {"iat": True},
        {"exp": float("inf")},
        {"iat": float("nan")},
        {"exp": 1},
    ],
)
def test_jwt_rejects_bad_claims(changes):
    now = datetime.now(UTC).timestamp()
    claims = {"sub": "demo@example.test", "iat": int(now), "exp": int(now) + 900}
    claims.update(changes)
    claims = {key: value for key, value in claims.items() if value is not None}
    token = jwt.encode(claims, SECRET, algorithm="HS256")
    with pytest.raises(DomainError):
        JwtCodec(SECRET).subject(token)


def test_jwt_rejects_wrong_key_algorithm_expiry_and_garbage():
    now = datetime.now(UTC)
    actor = Actor(1, "Demo", "demo@example.test", "STUDENT", True, now)
    codec = JwtCodec(SECRET)
    tokens = [
        JwtCodec("different-test-key-at-least-32-bytes-long").issue(actor, now),
        codec.issue(actor, now - timedelta(hours=1)),
        "bad",
        "x" * 8193,
        jwt.encode(
            {"sub": actor.email, "iat": now, "exp": now + timedelta(minutes=15)},
            SECRET,
            algorithm="HS384",
        ),
    ]
    for token in tokens:
        with pytest.raises(DomainError):
            codec.subject(token)


def test_settings_reject_weak_secret_wildcard_and_insecure_production():
    for values in [
        {"jwt_secret": "weak"},
        {"jwt_secret": SECRET, "cors_origins": ["*"]},
        {"jwt_secret": SECRET, "environment": "production", "cookie_secure": False},
    ]:
        with pytest.raises(ValidationError):
            AuthSettings(_env_file=None, **values)
    settings = AuthSettings(
        _env_file=None,
        jwt_secret=SECRET,
        environment="production",
        cookie_secure=True,
        cors_origins=["https://example.test"],
    )
    assert settings.cookie_secure


def test_utc_datetime_roundtrip_without_database():
    adapter = UtcDateTime()
    original = datetime(2026, 1, 1, 8, tzinfo=timezone(timedelta(hours=7)))
    stored = adapter.process_bind_param(original, None)
    assert stored == datetime(2026, 1, 1, 1)
    assert adapter.process_result_value(stored, None) == original
    with pytest.raises(ValueError):
        adapter.process_bind_param(datetime(2026, 1, 1), None)
