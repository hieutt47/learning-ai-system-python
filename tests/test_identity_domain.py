"""Pure standard-library tests; no database or JWT/password dependencies."""

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.modules.identity.domain.models import AuthSession, DomainError, User
from app.modules.identity.domain.service import IdentityService, hash_refresh_token

NOW = datetime(2026, 11, 16, tzinfo=UTC)
USER = User(1, "Demo User", "user@example.test", "stored-hash", "STUDENT", True, NOW, NOW)


class FakeUsers:
    def __init__(self, rows):
        self.rows = rows

    def get_by_email(self, email):
        return next((user for user in self.rows if user.email == email), None)

    def get_by_id(self, user_id):
        return next((user for user in self.rows if user.id == user_id), None)


class FakeSessions:
    def __init__(self, rows, locks):
        self.rows = rows
        self.locks = locks

    def get_for_update(self, token_hash):
        self.locks.append(token_hash)
        return next((row for row in self.rows if row.refresh_token_hash == token_hash), None)

    def add(self, session):
        self.rows.append(replace(session, id=len(self.rows) + 1))

    def save(self, session):
        assert self.locks, "Session update must follow a locking read"
        index = next(i for i, row in enumerate(self.rows) if row.id == session.id)
        self.rows[index] = session


class FakeUow:
    def __init__(self, store):
        self.store = store
        self.users = FakeUsers(store.users)
        self.sessions = FakeSessions(list(store.sessions), store.locks)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        # Only commit copies the isolated snapshot into the durable fake store.
        pass

    def commit(self):
        if self.store.fail_commit:
            raise RuntimeError("Persistence failed")
        self.store.sessions = list(self.sessions.rows)
        self.store.commits += 1


class Store:
    def __init__(self):
        self.users = [USER]
        self.sessions = []
        self.locks = []
        self.commits = 0
        self.fail_commit = False

    def __call__(self):
        return FakeUow(self)


class FakePasswords:
    def __init__(self):
        self.calls = []

    def verify(self, password, password_hash):
        self.calls.append((password, password_hash))
        return password_hash is not None and password == " secret "


class FakeTokens:
    def __init__(self):
        self.fail = False
        self.actors = []

    def issue(self, actor, now):
        if self.fail:
            raise RuntimeError("Token issuance failed")
        self.actors.append(actor)
        return "signed-access-token"


class IdentityDomainTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.passwords = FakePasswords()
        self.tokens = FakeTokens()
        self.service = IdentityService(self.store, self.passwords, self.tokens, lambda: NOW)

    def seed_session(self, **changes):
        session = AuthSession(
            1,
            1,
            hash_refresh_token("old-token"),
            NOW + timedelta(days=1),
            None,
            NOW - timedelta(days=1),
            NOW - timedelta(days=1),
        )
        session = replace(session, **changes)
        self.store.sessions = [session]
        return session

    def assert_code(self, code, operation):
        with self.assertRaises(DomainError) as caught:
            operation()
        self.assertEqual(code, caught.exception.code)

    def test_login_normalizes_email_preserves_password_and_stores_only_hash(self):
        result = self.service.login(" User@Example.Test ", " secret ")
        self.assertEqual("signed-access-token", result.access_token)
        self.assertEqual([(" secret ", "stored-hash")], self.passwords.calls)
        self.assertRegex(result.refresh_token, r"^[A-Za-z0-9_-]{43}$")
        session = self.store.sessions[0]
        self.assertEqual(hash_refresh_token(result.refresh_token), session.refresh_token_hash)
        self.assertNotEqual(result.refresh_token, session.refresh_token_hash)
        self.assertEqual(NOW + timedelta(days=7), session.expires_at)
        self.assertEqual(1, self.store.commits)

    def test_login_rejects_wrong_credentials_and_uses_missing_user_verification(self):
        self.assert_code("UNAUTHENTICATED", lambda: self.service.login(USER.email, "wrong"))
        self.assert_code(
            "UNAUTHENTICATED", lambda: self.service.login("unknown@example.test", " secret ")
        )
        self.assertIsNone(self.passwords.calls[-1][1])
        self.assertEqual([], self.store.sessions)

    def test_login_rejects_inactive_or_unverified_without_creating_session(self):
        for changed, code in [
            ({"is_active": False}, "ACCOUNT_INACTIVE"),
            ({"email_verified_at": None}, "EMAIL_NOT_VERIFIED"),
        ]:
            with self.subTest(code=code):
                self.store.users = [replace(USER, **changed)]
                self.assert_code(code, lambda: self.service.login(USER.email, " secret "))
                self.assertEqual([], self.store.sessions)
                self.assertEqual([], self.tokens.actors)

    def test_password_utf8_byte_limit_and_blank_fields(self):
        for email, password in [
            (" ", " secret "),
            (USER.email, " "),
            (USER.email, "x" * 73),
            (USER.email, "é" * 37),
        ]:
            with self.subTest(email=email, password=password):
                self.assert_code(
                    "VALIDATION_ERROR",
                    lambda email=email, password=password: self.service.login(email, password),
                )
        # No registration minimum; verifier, not length policy, rejects old short passwords.
        self.assert_code("UNAUTHENTICATED", lambda: self.service.login(USER.email, "x"))
        self.assertEqual("x", self.passwords.calls[-1][0])

    def test_refresh_locks_rotates_same_row_slides_expiry_and_rejects_old_token(self):
        old = self.seed_session()
        result = self.service.refresh("old-token")
        row = self.store.sessions[0]
        self.assertEqual([old.refresh_token_hash], self.store.locks)
        self.assertEqual(old.id, row.id)
        self.assertEqual(old.created_at, row.created_at)
        self.assertEqual(NOW, row.updated_at)
        self.assertEqual(NOW + timedelta(days=7), row.expires_at)
        self.assertEqual(hash_refresh_token(result.refresh_token), row.refresh_token_hash)
        self.assertNotEqual(old.refresh_token_hash, row.refresh_token_hash)
        self.assert_code("UNAUTHENTICATED", lambda: self.service.refresh("old-token"))
        self.service.refresh(result.refresh_token)
        self.assertEqual(2, self.store.commits)

    def test_refresh_rejects_absent_unknown_revoked_and_expired(self):
        for token in [None, "", " ", "unknown"]:
            with self.subTest(token=token):
                self.assert_code("UNAUTHENTICATED", lambda token=token: self.service.refresh(token))
        for changes in [
            {"revoked_at": NOW},
            {"expires_at": NOW},
            {"expires_at": NOW - timedelta(seconds=1)},
        ]:
            with self.subTest(changes=changes):
                old = self.seed_session(**changes)
                self.assert_code("UNAUTHENTICATED", lambda: self.service.refresh("old-token"))
                self.assertEqual([old], self.store.sessions)
        self.assertEqual(0, self.store.commits)

    def test_refresh_rejects_missing_inactive_unverified_user_without_rotation(self):
        for users, code in [
            ([], "UNAUTHENTICATED"),
            ([replace(USER, is_active=False)], "ACCOUNT_INACTIVE"),
            ([replace(USER, email_verified_at=None)], "EMAIL_NOT_VERIFIED"),
        ]:
            with self.subTest(code=code):
                old = self.seed_session()
                self.store.users = users
                self.assert_code(code, lambda: self.service.refresh("old-token"))
                self.assertEqual([old], self.store.sessions)
                self.assertEqual(0, self.store.commits)
                self.assertEqual([], self.tokens.actors)

    def test_token_issue_and_commit_failures_leave_refresh_unchanged(self):
        old = self.seed_session()
        self.tokens.fail = True
        with self.assertRaises(RuntimeError):
            self.service.refresh("old-token")
        self.assertEqual([old], self.store.sessions)
        self.tokens.fail = False
        self.store.fail_commit = True
        with self.assertRaises(RuntimeError):
            self.service.refresh("old-token")
        self.assertEqual([old], self.store.sessions)
        with self.assertRaises(RuntimeError):
            self.service.login(USER.email, " secret ")
        self.assertEqual([old], self.store.sessions)

    def test_logout_idempotent_and_expired_session_can_be_revoked(self):
        old = self.seed_session(expires_at=NOW - timedelta(seconds=1))
        self.service.logout("old-token")
        self.assertEqual(NOW, self.store.sessions[0].revoked_at)
        self.assertEqual(old.refresh_token_hash, self.store.sessions[0].refresh_token_hash)
        self.service.logout("old-token")
        for token in [None, "", " ", "unknown"]:
            self.service.logout(token)
        self.assertEqual(1, self.store.commits)

    def test_logout_does_not_require_active_or_verified_user(self):
        self.seed_session()
        self.store.users = [replace(USER, is_active=False, email_verified_at=None)]
        self.service.logout("old-token")
        self.assertEqual(NOW, self.store.sessions[0].revoked_at)

    def test_me_reads_live_user_role_without_exposing_hash_or_requiring_session(self):
        self.store.users = [replace(USER, role="ADMIN")]
        actor = self.service.me(USER.email)
        self.assertEqual("ADMIN", actor.role)
        self.assertFalse(hasattr(actor, "password_hash"))
        self.assertEqual([], self.store.sessions)
        self.assertEqual(0, self.store.commits)
        self.store.users = [replace(USER, is_active=False)]
        self.assert_code("ACCOUNT_INACTIVE", lambda: self.service.me(USER.email))
        self.assert_code("UNAUTHENTICATED", lambda: self.service.me("missing@example.test"))


if __name__ == "__main__":
    unittest.main()
