"""Minimal identity use cases. Transactions and cryptography are injected."""

import hashlib
import secrets
from dataclasses import replace
from datetime import timedelta

from .models import Actor, AuthenticationTokens, AuthSession, DomainError, User
from .ports import Clock, PasswordVerifier, TokenIssuer, UnitOfWorkFactory

REFRESH_LIFETIME = timedelta(days=7)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_active(user: User) -> None:
    if not user.is_active:
        raise DomainError("ACCOUNT_INACTIVE", "Account is inactive")


def require_verified(user: User) -> None:
    if user.email_verified_at is None:
        raise DomainError("EMAIL_NOT_VERIFIED", "Email has not been verified")


def unauthenticated() -> DomainError:
    return DomainError("UNAUTHENTICATED", "Authentication required or credentials are invalid")


class IdentityService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        passwords: PasswordVerifier,
        tokens: TokenIssuer,
        clock: Clock,
    ):
        self._uow_factory = uow_factory
        self._passwords = passwords
        self._tokens = tokens
        self._clock = clock

    def login(self, email: str, password: str) -> AuthenticationTokens:
        if not email.strip():
            raise DomainError("VALIDATION_ERROR", "Email must not be blank", field="email")
        if not password.strip() or len(password.encode("utf-8")) > 72:
            raise DomainError(
                "VALIDATION_ERROR",
                "Password must be nonblank and at most 72 UTF-8 bytes",
                field="password",
            )
        with self._uow_factory() as uow:
            user = uow.users.get_by_email(email.strip().lower())
            matches = self._passwords.verify(password, user.password_hash if user else None)
            if user is None or not matches:
                raise unauthenticated()
            require_active(user)
            require_verified(user)
            now = self._clock()
            access_token = self._tokens.issue(Actor.from_user(user), now)
            refresh_token = secrets.token_urlsafe(32)
            uow.sessions.add(
                AuthSession(
                    id=None,
                    user_id=user.id,
                    refresh_token_hash=hash_refresh_token(refresh_token),
                    expires_at=now + REFRESH_LIFETIME,
                    revoked_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            uow.commit()
            return AuthenticationTokens(access_token, refresh_token)

    def refresh(self, refresh_token: str | None) -> AuthenticationTokens:
        if not refresh_token or not refresh_token.strip():
            raise unauthenticated()
        with self._uow_factory() as uow:
            session = uow.sessions.get_for_update(hash_refresh_token(refresh_token))
            now = self._clock()
            if session is None or session.revoked_at is not None or session.expires_at <= now:
                raise unauthenticated()
            user = uow.users.get_by_id(session.user_id)
            if user is None:
                raise unauthenticated()
            require_active(user)
            require_verified(user)
            # Issue before saving: even a crypto/config failure cannot rotate the row.
            access_token = self._tokens.issue(Actor.from_user(user), now)
            replacement = secrets.token_urlsafe(32)
            uow.sessions.save(
                replace(
                    session,
                    refresh_token_hash=hash_refresh_token(replacement),
                    expires_at=now + REFRESH_LIFETIME,
                    updated_at=now,
                )
            )
            uow.commit()
            return AuthenticationTokens(access_token, replacement)

    def logout(self, refresh_token: str | None) -> None:
        if not refresh_token or not refresh_token.strip():
            return
        with self._uow_factory() as uow:
            session = uow.sessions.get_for_update(hash_refresh_token(refresh_token))
            if session is not None and session.revoked_at is None:
                now = self._clock()
                uow.sessions.save(replace(session, revoked_at=now, updated_at=now))
                uow.commit()

    def me(self, subject: str) -> Actor:
        """JWT adapter verifies signature/expiry and passes the email subject here."""
        with self._uow_factory() as uow:
            user = uow.users.get_by_email(subject)
            if user is None:
                raise unauthenticated()
            require_active(user)
            return Actor.from_user(user)
