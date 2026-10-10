from __future__ import annotations

import csv
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
from .paths import output_file
import sqlite3

from .common import job_folder, slug

STATUSES = {
    "pending": "Oczekuje", "running": "Przetwarzanie", "done": "Gotowe",
    "error": "Błąd", "unavailable": "Niedostępny", "login_required": "Wymaga logowania",
    "scheduled": "Zaplanowany", "live": "Trwa transmisja", "draft": "Szkic bez linku",
    "blocked": "Blokada YouTube",
}
RETRYABLE = ("error", "unavailable", "login_required", "scheduled", "live", "blocked")
SCOPE_KINDS = ("all", "youtube", "local")
BEGIN_WRITE = "BEGIN IMMEDIATE"
SELECT_CONTROL = "SELECT * FROM control WHERE id=1"


def scope_kind(value):
    if value not in SCOPE_KINDS:
        raise ValueError("Zakres kolejki musi mieć wartość all, youtube albo local.")
    return value


def scope_ids(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("Zakres nagrań musi być listą identyfikatorów.") from exc
    if not isinstance(value, (list, tuple, set)) or any(type(item) is not int or item <= 0 for item in value):
        raise ValueError("Zakres nagrań musi zawierać dodatnie całkowite identyfikatory.")
    return sorted(set(value))


def scope_filter(kind, ids, *, enabled=False):
    """Build a parameterized predicate shared by claiming and scoped retries."""
    kind, ids = scope_kind(kind), scope_ids(ids)
    clauses, parameters = [], []
    if enabled:
        clauses.append("enabled=1")
    if kind != "all":
        clauses.append("kind=?")
        parameters.append(kind)
    if ids:
        clauses.append("id IN (" + ",".join("?" for _ in ids) + ")")
        parameters.extend(ids)
    return " AND ".join(clauses) or "1=1", parameters


def in_scope(job, control):
    ids = scope_ids(control["scope_ids"])
    return (control["scope_kind"] == "all" or job["kind"] == control["scope_kind"]) and (not ids or job["id"] in ids)


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
                    folder TEXT NOT NULL, output_root TEXT, status TEXT NOT NULL DEFAULT 'pending',
                    enabled INTEGER DEFAULT 1, language TEXT DEFAULT 'auto',
                    audio_track INTEGER DEFAULT 0, stage TEXT DEFAULT '',
                    progress REAL DEFAULT 0, error TEXT DEFAULT '',
                    attempts INTEGER DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS control (
                    id INTEGER PRIMARY KEY CHECK(id=1), stop INTEGER DEFAULT 1,
                    state TEXT DEFAULT 'idle', pid INTEGER, heartbeat TEXT,
                    current_id INTEGER, message TEXT DEFAULT '',
                    scope_kind TEXT NOT NULL DEFAULT 'all', scope_ids TEXT NOT NULL DEFAULT '[]'
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
            # Serialize inspection and ALTER: two GUI processes can open an old
            # database at the same time without racing the same schema change.
            db.execute(BEGIN_WRITE)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(control)")}
            if "scope_kind" not in columns:
                db.execute("ALTER TABLE control ADD COLUMN scope_kind TEXT NOT NULL DEFAULT 'all'")
            if "scope_ids" not in columns:
                db.execute("ALTER TABLE control ADD COLUMN scope_ids TEXT NOT NULL DEFAULT '[]'")
            job_columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "output_root" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN output_root TEXT")

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
                if row.get("output_root"):
                    from .local_outputs import compact_folder
                    folder = compact_folder(row["title"], row["identity"], row["output_root"])
                cursor = db.execute('''INSERT OR IGNORE INTO jobs
                    (identity,kind,title,source,date,visibility,duration,source_meta,folder,output_root,
                     status,enabled,stage,error,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (row["identity"], row["kind"], row["title"], row["source"],
                     row.get("date", ""), row.get("visibility", ""), row.get("duration"),
                     json.dumps(row.get("meta", {}), ensure_ascii=False), folder, row.get("output_root"),
                     row.get("status", "pending"), int(row.get("status") != "draft"),
                     row.get("stage", ""), row.get("error", ""), stamp, stamp))
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
        allowed = {"stop", "state", "pid", "heartbeat", "current_id", "message", "scope_kind", "scope_ids"}
        if not values.keys() <= allowed:
            raise ValueError("Niepoprawne pola sterowania.")
        if "scope_kind" in values:
            values["scope_kind"] = scope_kind(values["scope_kind"])
        if "scope_ids" in values:
            values["scope_ids"] = json.dumps(scope_ids(values["scope_ids"]))
        with self.connect() as db:
            if values:
                db.execute("UPDATE control SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=1",
                           tuple(values.values()))
            return dict(db.execute(SELECT_CONTROL).fetchone())

    def claim(self):
        # Stop and claiming the next film are serialized in the same database.
        with self.connect() as db:
            db.execute(BEGIN_WRITE)
            control = db.execute(SELECT_CONTROL).fetchone()
            if control["stop"]:
                return None
            predicate, parameters = scope_filter(control["scope_kind"], control["scope_ids"], enabled=True)
            row = db.execute("SELECT * FROM jobs WHERE status='pending' AND " + predicate +
                " ORDER BY CASE WHEN date='' THEN 1 ELSE 0 END,date,id LIMIT 1", parameters).fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running',error='',updated=? WHERE id=?", (now(), row["id"]))
            db.execute("UPDATE control SET current_id=? WHERE id=1", (row["id"],))
            return dict(row)

    def recover(self):
        # Call only with the operating-system worker lock held.
        with self.connect() as db:
            control = db.execute(SELECT_CONTROL).fetchone()
            predicate, parameters = scope_filter(control["scope_kind"], control["scope_ids"])
            db.execute("UPDATE jobs SET status='pending',stage='Wznawianie' WHERE status='running' AND " + predicate,
                       parameters)

    def start(self, kind="all", job_ids=None):
        kind = scope_kind(kind)
        ids = [] if job_ids is None else scope_ids(job_ids)
        if job_ids is not None and not ids:
            raise ValueError("Zaznacz przynajmniej jedno nagranie do uruchomienia.")
        predicate, parameters = scope_filter(kind, ids, enabled=True)
        with self.connect() as db:
            db.execute(BEGIN_WRITE)
            if ids:
                selected = db.execute("SELECT id,kind FROM jobs WHERE id IN (" + ",".join("?" for _ in ids) + ")", ids).fetchall()
                if len(selected) != len(ids) or any(kind != "all" and row["kind"] != kind for row in selected):
                    raise ValueError("Wybrane identyfikatory nie należą do wskazanego rodzaju kolejki.")
            db.execute("UPDATE jobs SET status='pending' WHERE (status='blocked' OR "
                       "(status='error' AND stage='Wymaga działania')) AND " + predicate, parameters)
            db.execute("UPDATE control SET stop=0,message='',scope_kind=?,scope_ids=? WHERE id=1",
                       (kind, json.dumps(ids)))

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

    def export_csv(self, path, kind=None):
        path = output_file(path, suffix=".csv")
        if kind is not None:
            kind = scope_kind(kind)
        rows = self.jobs()
        if kind is not None and kind != "all":
            rows = [row for row in rows if row["kind"] == kind]
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, delimiter=";")
            writer.writerow(["ID", "Tytuł", "Data", "Typ", "Status", "Etap", "Postęp %", "Błąd", "Źródło", "Wyniki", "Czas trwania (s)"])
            for job in rows:
                # Prevent spreadsheet formulas from imported titles / error text.
                def safe(value):
                    value = str(value)
                    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
                writer.writerow(map(safe, [job["identity"], job["title"], job["date"], job["kind"],
                    STATUSES[job["status"]], job["stage"], round(job["progress"], 1), job["error"],
                    job["source"], str(job_folder(self.root, job)), job["duration"] if job["duration"] is not None else ""]))

    def change_output(self, ids, mode, custom=""):
        from .local_outputs import compact_folder, output_root_for
        if not ids:
            raise ValueError("Zaznacz nagrania, dla których chcesz zmienić miejsce wyników.")
        with self.connect() as db:
            db.execute(BEGIN_WRITE)
            control = db.execute(SELECT_CONTROL).fetchone()
            if control["state"] == "running" or not control["stop"]:
                raise ValueError("Najpierw dokończ bieżące nagranie i zatrzymaj sesję.")
            selected = []
            for ident in ids:
                row = db.execute("SELECT * FROM jobs WHERE id=?", (ident,)).fetchone()
                if row is None:
                    raise ValueError("Nie znaleziono wybranego nagrania.")
                job = dict(row)
                old = job_folder(self.root, job)
                if job["kind"] != "local" or job["status"] in {"running", "done", "draft"} or job["attempts"] or (old.exists() and any(old.iterdir())):
                    raise ValueError("Miejsce można zmienić tylko dla lokalnych nagrań bez rozpoczętego przetwarzania: " + job["title"])
                base = output_root_for(job["source"], self.root, mode, custom)
                selected.append((str(base), compact_folder(job["title"], job["identity"], base), now(), ident))
            db.executemany("UPDATE jobs SET output_root=?,folder=?,updated=? WHERE id=?", selected)
        return len(selected)
