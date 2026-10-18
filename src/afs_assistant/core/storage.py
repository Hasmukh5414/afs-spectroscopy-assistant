"""SQLite transactions with browser-owner scoping and revision checks."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, revision INTEGER NOT NULL,
                    confirmed INTEGER NOT NULL DEFAULT 0, text TEXT NOT NULL,
                    facts TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS releases (
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL, corpus TEXT NOT NULL,
                    model TEXT NOT NULL, vectors TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS current_release (slot INTEGER PRIMARY KEY CHECK(slot=1), id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chat_turns (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
                    request_id TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL,
                    release TEXT NOT NULL, answer TEXT, sources TEXT NOT NULL DEFAULT '{}',
                    trace TEXT NOT NULL DEFAULT '[]', created TEXT NOT NULL,
                    UNIQUE(case_id,request_id));
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_chat_turn
                    ON chat_turns(case_id) WHERE status='pending';
                CREATE TABLE IF NOT EXISTS chat_starts (
                    owner TEXT NOT NULL, request_id TEXT NOT NULL, case_id TEXT NOT NULL REFERENCES cases(id),
                    PRIMARY KEY(owner,request_id));
            """)

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=5000")
            yield db

    def _case(self, db, case_id, owner):
        row = db.execute("SELECT * FROM cases WHERE id=? AND owner=?", (case_id, owner)).fetchone()
        if row is None:
            raise NotFound("Case not found")
        return {"id": row["id"], "text": row["text"], "revision": row["revision"], "updated": row["updated"]}

    def create_case(self, owner, text, request_id=None):
        case_id = uuid4().hex
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT case_id FROM chat_starts WHERE owner=? AND request_id=?", (owner, request_id)).fetchone() if request_id else None
            if previous:
                case_id = previous["case_id"]
            else:
                db.execute("INSERT INTO cases (id,owner,revision,confirmed,text,facts,updated) VALUES (?,?,1,0,?,'{}',?)", (case_id, owner, text, now()))
                if request_id:
                    db.execute("INSERT INTO chat_starts VALUES (?,?,?)", (owner, request_id, case_id))
        return self.get_case(case_id, owner)

    def get_case(self, case_id, owner):
        with self.connection() as db:
            return self._case(db, case_id, owner)

    def list_cases(self, owner):
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,text,revision,confirmed,updated FROM cases WHERE owner=? ORDER BY updated DESC LIMIT 50", (owner,))]

    def recover_interrupted(self):
        with self.connection() as db:
            db.execute("UPDATE chat_turns SET status='failed' WHERE status='pending'")

    def get_chat(self, case_id, owner):
        with self.connection() as db:
            case = self._case(db, case_id, owner)
            turns = []
            for row in db.execute("SELECT * FROM chat_turns WHERE case_id=? ORDER BY rowid", (case_id,)):
                turn = dict(row)
                for key in ("answer", "sources", "trace"):
                    turn[key] = json.loads(turn[key]) if turn[key] else None
                turns.append(turn)
        return {"id": case_id, "text": case["text"], "revision": case["revision"], "turns": turns}

    def start_chat_turn(self, case_id, owner, expected, text, request_id, release):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._case(db, case_id, owner)
            previous = db.execute("SELECT id FROM chat_turns WHERE case_id=? AND request_id=?", (case_id, request_id)).fetchone()
            if previous:
                return previous["id"], False
            if case["revision"] != expected:
                raise Conflict("Conversation changed. Reload before sending.")
            turn_id = uuid4().hex
            try:
                db.execute("INSERT INTO chat_turns (id,case_id,request_id,text,status,release,created) VALUES (?,?,?,?,'pending',?,?)",
                           (turn_id, case_id, request_id, text, release, now()))
            except sqlite3.IntegrityError as exc:
                raise Conflict("An answer is already being generated in this conversation.") from exc
            db.execute("UPDATE cases SET revision=revision+1,updated=? WHERE id=?", (now(), case_id))
        return turn_id, True

    def retry_chat_turn(self, case_id, turn_id, owner, expected):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            case = self._case(db, case_id, owner)
            row = db.execute("SELECT * FROM chat_turns WHERE case_id=? ORDER BY rowid DESC LIMIT 1", (case_id,)).fetchone()
            if case["revision"] != expected or row is None or row["id"] != turn_id or row["status"] != "failed":
                raise Conflict("Only the latest failed answer can be retried. Reload the conversation.")
            db.execute("UPDATE chat_turns SET status='pending',trace='[]' WHERE id=?", (turn_id,))
            db.execute("UPDATE cases SET revision=revision+1,updated=? WHERE id=?", (now(), case_id))
        return row["release"]

    def finish_chat_turn(self, turn_id, answer, sources, trace, status="answered"):
        with self.connection() as db:
            db.execute("UPDATE chat_turns SET status=?,answer=?,sources=?,trace=? WHERE id=? AND status='pending'",
                       (status, answer.model_dump_json() if answer else None, json.dumps(sources), json.dumps(trace), turn_id))

    def progress_chat_turn(self, turn_id, trace):
        with self.connection() as db:
            db.execute("UPDATE chat_turns SET trace=? WHERE id=? AND status='pending'",
                       (json.dumps(trace), turn_id))

    def publish(self, release, digest, corpus, model, vectors):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT digest FROM releases WHERE id=?", (release,)).fetchone()
            if old and old[0] != digest:
                raise Conflict("Evidence release already exists with different content or model. Use a new release ID.")
            db.execute("INSERT OR IGNORE INTO releases VALUES (?,?,?,?,?)", (release, digest, corpus, model, json.dumps(vectors)))
            db.execute("INSERT INTO current_release VALUES (1,?) ON CONFLICT(slot) DO UPDATE SET id=excluded.id", (release,))

    def corpus(self, release=None):
        with self.connection() as db:
            if release is None:
                row = db.execute("SELECT r.* FROM releases r JOIN current_release c ON r.id=c.id").fetchone()
            else:
                row = db.execute("SELECT * FROM releases WHERE id=?", (release,)).fetchone()
            return dict(row) if row else None

