"""BCrypt and fixed-algorithm HS256 adapters."""

import math
from datetime import datetime, timedelta

import bcrypt
import jwt

from app.modules.identity.domain.models import Actor, DomainError


class BCryptVerifier:
    # Public, non-secret dummy hash, cost 10. Missing accounts still perform BCrypt work.
    _dummy_hash = bcrypt.hashpw(b"dummy-password-never-accepted", bcrypt.gensalt(rounds=10))

    def verify(self, password: str, password_hash: str | None) -> bool:
        encoded = password.encode("utf-8")
        if len(encoded) > 72:
            return False
        try:
            candidate = password_hash.encode("ascii") if password_hash else self._dummy_hash
            matches = bcrypt.checkpw(encoded, candidate)
        except (ValueError, TypeError, UnicodeError):
            return False
        return password_hash is not None and matches


class JwtCodec:
    def __init__(self, secret: str):
        if len(secret.encode("utf-8")) < 32:
            raise ValueError("JWT secret requires at least 32 bytes")
        self._secret = secret

    def issue(self, actor: Actor, now: datetime) -> str:
        return jwt.encode(
            {
                "sub": actor.email,
                "iat": now,
                "exp": now + timedelta(minutes=15),
                "role": [f"ROLE_{actor.role}"],
            },
            self._secret,
            algorithm="HS256",
        )

    def subject(self, token: str) -> str:
        try:
            if len(token) > 8192:
                raise ValueError("Token too long")
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                options={"require": ["exp", "iat", "sub"]},
            )
            if (
                not isinstance(claims["sub"], str)
                or not claims["sub"]
                or type(claims["exp"]) not in {int, float}
                or type(claims["iat"]) not in {int, float}
                or not math.isfinite(claims["exp"])
                or not math.isfinite(claims["iat"])
                or claims["exp"] <= claims["iat"]
            ):
                raise ValueError("Invalid claims")
            return claims["sub"]
        except (jwt.PyJWTError, ValueError, TypeError, OverflowError):
            raise DomainError(
                "UNAUTHENTICATED", "Authentication required or credentials are invalid"
            ) from None
