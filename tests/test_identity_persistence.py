from sqlalchemy.dialects import mysql

from app.modules.identity.infrastructure.persistence import SqlSessions


def test_session_lookup_compiles_select_for_update_without_database():
    class RecordingSession:
        statement = None

        def scalar(self, statement):
            self.statement = statement
            return None

    session = RecordingSession()
    assert SqlSessions(session).get_for_update("a" * 64) is None
    compiled = str(session.statement.compile(dialect=mysql.dialect()))
    assert "FOR UPDATE" in compiled
    assert "auth_sessions.refresh_token_hash =" in compiled
    assert "a" * 64 not in compiled
