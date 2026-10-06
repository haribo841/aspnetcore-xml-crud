from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import hashlib
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

from .common import digest
from .store import now

MEDIA = {".wav", ".mp3", ".mp4", ".mkv", ".webm", ".m4a", ".aac", ".flac",
         ".opus", ".ogg", ".aiff", ".aif", ".wma", ".wmv", ".mov", ".avi",
         ".m4v", ".mts", ".m2ts", ".ts", ".mpeg", ".mpg", ".3gp"}


def video_id(url, supplied=""):
    supplied = str(supplied or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", supplied):
        return supplied
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").lower()
    candidate = ""
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "studio.youtube.com"}:
        candidate = parse_qs(parsed.query).get("v", [""])[0]
        match = re.search(r"/(?:shorts|live|embed|video)/([A-Za-z0-9_-]{11})(?:/|$)", parsed.path)
        if match:
            candidate = match[1]
    return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) else ""


def duration_seconds(value):
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, time):
        return value.hour * 3600 + value.minute * 60 + value.second
    if isinstance(value, (int, float)):
        return float(value) * 86400
    if value:
        try:
            result = 0
            for part in str(value).split(":"):
                result = result * 60 + float(part)
            return result
        except ValueError:
            pass
    return None


def import_xlsx(store, source):
    from openpyxl import load_workbook
    from openpyxl.utils.datetime import from_excel

    source = Path(source).resolve(strict=True)
    before = digest(source)
    # Read-only source file. It is never saved, renamed or modified.
    workbook = load_workbook(source, read_only=False, data_only=True)
    try:
        if "Materiały" not in workbook.sheetnames:
            raise ValueError("Brak arkusza Materiały.")
        sheet = workbook["Materiały"]
        header, header_row = None, None
        for row in sheet.iter_rows(min_row=1, max_row=min(100, sheet.max_row)):
            names = {str(cell.value).strip().casefold(): i for i, cell in enumerate(row) if cell.value is not None}
            if {"tytuł", "link", "id filmu"} <= names.keys():
                header, header_row = names, row[0].row
                break
        if header is None:
            raise ValueError("Nie znaleziono nagłówków Tytuł, Link i ID filmu.")
        result = []
        drafts = 0
        for row in sheet.iter_rows(min_row=header_row + 1):
            def get(name, default=""):
                index = header.get(name.casefold())
                return row[index].value if index is not None and row[index].value is not None else default
            title = str(get("Tytuł")).strip()
            if not title:
                continue
            link = get("Link")
            cell = row[header["link"]]
            if cell.hyperlink and cell.hyperlink.target:
                link = cell.hyperlink.target
            ident = video_id(link, get("ID filmu"))
            date_value = get("Data")
            if isinstance(date_value, (int, float)):
                date_value = from_excel(date_value, workbook.epoch)
            date_text = date_value.isoformat() if isinstance(date_value, (datetime, date)) else str(date_value)
            meta = {"catalog": str(source), "row": row[0].row, "ordinal": get("Lp."),
                    "kind": str(get("Typ")), "source_status": str(get("Status")),
                    "date_meaning": str(get("Znaczenie daty"))}
            if not ident:
                drafts += 1
                # Stable across a repeated import of the same source table.
                key = hashlib.sha256(f"{source.name}|{get('Lp.')}|{title}".encode()).hexdigest()[:24]
                identity, status = "draft:" + key, "draft"
            else:
                identity, status = "yt:" + ident, "pending"
            result.append({"identity": identity, "kind": "youtube", "title": title,
                           "source": "https://www.youtube.com/watch?v=" + ident if ident else "",
                           "date": date_text, "visibility": str(get("Widoczność")),
                           "duration": duration_seconds(get("Długość", None)), "meta": meta,
                           "status": status})
    finally:
        workbook.close()
    if digest(source) != before:
        raise RuntimeError("Arkusz zmienił się podczas odczytu. Zamknij edycję i ponów import.")
    inserted = store.add_many(result)
    note = f"{len(result)} wierszy, {drafts} bez poprawnego ID/linku."
    with store.connect() as db:
        db.execute("INSERT INTO imports(path,sha256,imported,rows,notes) VALUES(?,?,?,?,?)",
                   (str(source), before, now(), len(result), note))
    return {"rows": len(result), "added": inserted, "drafts": drafts, "sha256": before, "note": note}


def import_local(store, paths, progress=lambda message: None):
    files = set()
    for source in paths:
        path = Path(source).resolve(strict=True)
        if path.is_dir():
            files.update(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA)
        elif path.suffix.lower() in MEDIA:
            files.add(path)
    count = 0
    for index, path in enumerate(sorted(files), 1):
        progress(f"Identyfikacja plików: {index}/{len(files)}: {path.name}")
        stat = path.stat()
        with store.connect() as db:
            cached = db.execute("SELECT * FROM local_cache WHERE path=?", (str(path),)).fetchone()
        if cached and cached["size"] == stat.st_size and cached["mtime_ns"] == stat.st_mtime_ns:
            checksum = cached["sha256"]
        else:
            checksum = digest(path)
            after = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError(f"Plik zmienił się podczas importu: {path}")
            with store.connect() as db:
                db.execute("INSERT OR REPLACE INTO local_cache VALUES(?,?,?,?)",
                           (str(path), stat.st_size, stat.st_mtime_ns, checksum))
        count += store.add_many([{"identity": "local:" + checksum, "kind": "local",
                "source": str(path), "title": path.stem,
                "date": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "meta": {"sha256": checksum, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}}])
    return {"files": len(files), "added": count}
