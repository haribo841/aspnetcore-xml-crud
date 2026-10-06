from __future__ import annotations

import csv
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .common import slug

STATUSES = {
    "pending": "Oczekuje", "running": "Przetwarzanie", "done": "Gotowe",
    "error": "Błąd", "unavailable": "Niedostępny", "login_required": "Wymaga logowania",
    "scheduled": "Zaplanowany", "live": "Trwa transmisja", "draft": "Szkic bez linku",
    "blocked": "Blokada YouTube",
}
RETRYABLE = ("error", "unavailable", "login_required", "scheduled", "live", "blocked")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "kolejka.sqlite3"
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY, identity TEXT UNIQUE NOT NULL,
                    kind TEXT NOT NULL, title TEXT NOT NULL, source TEXT NOT NULL,
                    date TEXT DEFAULT '', visibility TEXT DEFAULT '',
                    duration REAL, source_meta TEXT DEFAULT '{}',
                    folder TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    enabled INTEGER DEFAULT 1, language TEXT DEFAULT 'auto',
                    audio_track INTEGER DEFAULT 0, stage TEXT DEFAULT '',
                    progress REAL DEFAULT 0, error TEXT DEFAULT '',
                    attempts INTEGER DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS control (
                    id INTEGER PRIMARY KEY CHECK(id=1), stop INTEGER DEFAULT 1,
                    state TEXT DEFAULT 'idle', pid INTEGER, heartbeat TEXT,
                    current_id INTEGER, message TEXT DEFAULT ''
                );
                INSERT OR IGNORE INTO control(id) VALUES(1);
                CREATE TABLE IF NOT EXISTS imports (
                    id INTEGER PRIMARY KEY, path TEXT, sha256 TEXT, imported TEXT,
                    rows INTEGER, notes TEXT
                );
                CREATE TABLE IF NOT EXISTS local_cache (
                    path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, sha256 TEXT
                );
            ''')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def add_many(self, rows):
        count = 0
        with self.connect() as db:
            for row in rows:
                stamp = now()
                folder = slug(row["title"]) + "__" + row["identity"].replace(":", "-")
                cursor = db.execute('''INSERT OR IGNORE INTO jobs
                    (identity,kind,title,source,date,visibility,duration,source_meta,folder,
                     status,enabled,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (row["identity"], row["kind"], row["title"], row["source"],
                     row.get("date", ""), row.get("visibility", ""), row.get("duration"),
                     json.dumps(row.get("meta", {}), ensure_ascii=False), folder,
                     row.get("status", "pending"), int(row.get("status") != "draft"), stamp, stamp))
                count += cursor.rowcount
                # A duplicate local file can be used if its old path disappeared.
                old = db.execute("SELECT source,status FROM jobs WHERE identity=?", (row["identity"],)).fetchone()
                if row["kind"] == "local" and not Path(old["source"]).exists():
                    db.execute("UPDATE jobs SET source=?,updated=? WHERE identity=?",
                               (row["source"], stamp, row["identity"]))
        return count

    def jobs(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM jobs ORDER BY CASE WHEN date='' THEN 1 ELSE 0 END,date,id")]

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def update(self, job_id, **values):
        allowed = {"stage", "progress", "error", "status", "duration", "attempts"}
        if not values.keys() <= allowed:
            raise ValueError("Niepoprawne pola aktualizacji zadania.")
        values["updated"] = now()
        with self.connect() as db:
            db.execute("UPDATE jobs SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?",
                       (*values.values(), job_id))

    def control(self, **values):
        allowed = {"stop", "state", "pid", "heartbeat", "current_id", "message"}
        if not values.keys() <= allowed:
            raise ValueError("Niepoprawne pola sterowania.")
        with self.connect() as db:
            if values:
                db.execute("UPDATE control SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=1",
                           tuple(values.values()))
            return dict(db.execute("SELECT * FROM control WHERE id=1").fetchone())

    def claim(self):
        # Stop and claiming the next film are serialized in the same database.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT stop FROM control WHERE id=1").fetchone()[0]:
                return None
            row = db.execute('''SELECT * FROM jobs WHERE status='pending' AND enabled=1
                ORDER BY CASE WHEN date='' THEN 1 ELSE 0 END,date,id LIMIT 1''').fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running',error='',updated=? WHERE id=?", (now(), row["id"]))
            db.execute("UPDATE control SET current_id=? WHERE id=1", (row["id"],))
            return dict(row)

    def recover(self):
        # Call only with the operating-system worker lock held.
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='pending',stage='Wznawianie' WHERE status='running'")

    def start(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='pending' WHERE status='blocked' OR (status='error' AND stage='Wymaga działania')")
            db.execute("UPDATE control SET stop=0,message='' WHERE id=1")

    def retry(self, ids=None):
        with self.connect() as db:
            query = "UPDATE jobs SET status='pending',error='',updated=? WHERE status IN (" + ",".join("?" for _ in RETRYABLE) + ")"
            args = [now(), *RETRYABLE]
            if ids:
                query += " AND id IN (" + ",".join("?" for _ in ids) + ")"
                args.extend(ids)
            return db.execute(query, args).rowcount

    def configure(self, ids, **values):
        if not values.keys() <= {"enabled", "language", "audio_track"}:
            raise ValueError("Niepoprawne ustawienie zadania.")
        if not ids:
            return
        with self.connect() as db:
            db.execute("UPDATE jobs SET " + ",".join(f"{key}=?" for key in values) +
                       " WHERE status NOT IN ('running','done','draft') AND id IN (" +
                       ",".join("?" for _ in ids) + ")", (*values.values(), *ids))

    def export_csv(self, path):
        rows = self.jobs()
        with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, delimiter=";")
            writer.writerow(["ID", "Tytuł", "Data", "Typ", "Status", "Etap", "Postęp %", "Błąd", "Źródło", "Wyniki"])
            for job in rows:
                # Prevent spreadsheet formulas from imported titles / error text.
                def safe(value):
                    value = str(value)
                    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
                writer.writerow(map(safe, [job["identity"], job["title"], job["date"], job["kind"],
                    STATUSES[job["status"]], job["stage"], round(job["progress"], 1), job["error"],
                    job["source"], str(self.root / "wyniki" / job["folder"])]))
