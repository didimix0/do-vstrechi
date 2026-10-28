import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3


class StaleSessionError(RuntimeError):
    pass


class SessionStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, document TEXT NOT NULL)")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, session: dict) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                       (session["id"], session["revision"], json.dumps(session, ensure_ascii=False)))

    def get(self, session_id: str) -> dict:
        with self._connect() as db:
            row = db.execute("SELECT document FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise KeyError("Сессия не найдена.")
        return json.loads(row[0])

    def recent(self, limit: int = 12) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT document FROM sessions ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def save(self, session: dict, expected_revision: int) -> dict:
        saved = dict(session, revision=expected_revision + 1)
        with self._connect() as db:
            updated = db.execute("UPDATE sessions SET revision = ?, document = ? WHERE id = ? AND revision = ?",
                                 (saved["revision"], json.dumps(saved, ensure_ascii=False), session["id"], expected_revision))
            if updated.rowcount != 1:
                raise StaleSessionError("Сессия уже изменилась. Загрузите актуальную версию.")
        return saved

