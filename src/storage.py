import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    """Each batch mutation is an atomic SQLite transaction."""

    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, body TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT body FROM batches ORDER BY rowid DESC")]

    def get(self, bid):
        with self.connect() as db:
            row = db.execute("SELECT body FROM batches WHERE id=?", (bid,)).fetchone()
        if not row:
            raise LookupError("批次不存在")
        return json.loads(row[0])

    def create(self, batch, force_new=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT body FROM batches"):
                existing = json.loads(row[0])
                if not force_new and existing.get("import_fingerprint") == batch["import_fingerprint"]:
                    return existing
            db.execute("INSERT INTO batches VALUES (?,?)", (batch["batch_id"], json.dumps(batch, ensure_ascii=False)))
        return batch

    def mutate(self, bid, change):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM batches WHERE id=?", (bid,)).fetchone()
            if not row:
                raise LookupError("批次不存在")
            batch = json.loads(row[0])
            change(batch)
            db.execute("UPDATE batches SET body=? WHERE id=?", (json.dumps(batch, ensure_ascii=False), bid))
        return batch
