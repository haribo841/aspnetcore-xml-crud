from __future__ import annotations

import importlib.metadata
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from .asr import ASR
from .common import (APP, NO_WINDOW, ResourceError, WorkerLock, application_python, atomic_json, check_disk,
                     digest, job_folder, read_json, remove_work_file, settings, signature,
                     windows_sleep_guard)
from .diarization import run_diarization, validate_diarization
from .exporter import export, verify_outputs
from .result_paths import assert_output_owner
from .media import MediaError, decode, download
from .speakers import assign_words, cues_from_words, merge_speakers
from .store import Store, now
from .uvr import archive_source, separate, validate_uvr
from .youtube_access import preferences as youtube_preferences


def preflight(config, root):
    from transcribe import validate_model
    try:
        validate_model(Path(config["model"]))
    except Exception as exc:
        raise ResourceError("Model Whisper nie jest gotowy: " + str(exc)) from exc
    for name in ("ffmpeg", "ffprobe", "node"):
        if not Path(config[name]).is_file():
            raise ResourceError(f"Brak programu {name}: {config[name]}")
    if config["diarization"]:
        validate_diarization(config)
    if config.get("uvr_enabled"):
        validate_uvr(config)
    check_disk(root, reserve_gb=config["min_free_gb"])
    for size, overlap in ((config["asr_seconds"], config["asr_context"]), (config["diar_seconds"], config["diar_context"])):
        if size <= 0 or overlap < 0 or overlap >= size / 2:
            raise ResourceError("Niepoprawne ustawienia długości bloków.")


def cleanup(work, job):
    work = Path(work)
    # Never delete source paths. Only owned intermediate files in this job folder.
    for name in ("audio.f32le", "audio.f32le.partial"):
        remove_work_file(work / name, work)
    if job["kind"] == "youtube":
        for path in work.glob("download.*"):
            if path.suffix != ".json":
                remove_work_file(path, work)
    # Once the complete vocal file and exports are verified, per-block FLACs
    # are disposable. Their manifests remain as processing evidence.
    for path in (work / "uvr").glob("*.flac"):
        remove_work_file(path, work)


class Worker:
    def __init__(self, root, processor=None):
        self.root = Path(root)
        self.store = Store(root)
        self.config = settings(root)
        self.asr = ASR(self.config, root)
        self.processor = processor or self.process
        self.last_update = 0
        self.current = None

    def progress(self, stage, percent):
        current = time.monotonic()
        if current - self.last_update < .5 and percent < 100:
            return
        self.last_update = current
        if self.current:
            self.store.update(self.current["id"], stage=stage, progress=float(percent))

    def process(self, job):
        config = self.config
        folder = job_folder(self.root, job)
        assert_output_owner(folder, job["identity"])
        work = folder / "robocze"
        work.mkdir(parents=True, exist_ok=True)
        execution = signature({"identity": job["identity"], "language": job["language"],
                               "track": job["audio_track"], "config": config, "format": 1})
        if verify_outputs(folder, job["identity"], execution):
            # Handles a crash after export but before the SQLite commit / cleanup.
            self.store.update(job["id"], status="done", stage="Gotowe", progress=100)
            cleanup(work, job)
            return
        if job["kind"] == "youtube":
            source = download(job, work, config, self.progress, youtube_preferences(self.root))
        else:
            source = Path(job["source"])
            if not source.is_file():
                raise MediaError("Nie znaleziono pliku lokalnego. Dodaj ponownie jego obecną lokalizację.")
            self.progress("Sprawdzanie oryginalnego pliku", 0)
            if digest(source) != job["identity"].split(":", 1)[1]:
                raise MediaError("Zawartość pliku lokalnego zmieniła się. Dodaj plik ponownie jako nowe nagranie.")
        retained, uvr_report = [], None
        if config.get("keep_audio") or config.get("uvr_enabled"):
            self.progress("Zachowywanie źródłowej ścieżki audio", 0)
            archived = archive_source(source, folder, job["audio_track"], config)
            retained.append(str(archived.relative_to(folder)))
        if config.get("uvr_enabled"):
            source, uvr_report = separate(source, work, folder, job["audio_track"], config, self.progress)
            retained.append(str(source.relative_to(folder)))
        pcm, duration, pcm_key = decode(source, work, config, 0 if config.get("uvr_enabled") else job["audio_track"], self.progress)
        self.store.update(job["id"], duration=duration)
        asr = self.asr.run(pcm, duration, pcm_key, job, work, self.progress)
        if config["diarization"]:
            diar_parts = run_diarization(pcm, duration, pcm_key, work, config, self.progress)
            self.progress("Łączenie mówców i przypisywanie słów", 0)
            diar = merge_speakers(diar_parts)
            words = assign_words(asr["words"], diar)
        else:
            diar_parts, diar = [], {"enabled": False, "turns": [], "exclusive": [], "speakers": []}
            words = [{**word, "speaker": "Mówca nieustalony", "overlap": []} for word in asr["words"]]
        report = {"schema": 1, "identity": job["identity"], "title": job["title"],
                  "source": job["source"], "source_metadata": json.loads(job["source_meta"]),
                  "created": now(), "duration": duration, "language": job["language"],
                  "sample_rate": 16000, "audio_track": job["audio_track"],
                  "settings": config, "asr": asr, "diarization": diar, "uvr": uvr_report,
                  "retained_audio": retained,
                  "diarization_blocks": diar_parts, "words": words, "segments": cues_from_words(words),
                  "notes": ["Etykiety mówców są automatyczne i dotyczą wyłącznie tego nagrania.",
                            "Łączenie słów i mówców na granicach bloków jest heurystyczne.",
                            "Automatyczny język może być zawodny w krótkich i mieszanych językowo fragmentach."],
                  "review_warnings": [{"start": w["start"], "end": w["end"], "text": w["text"],
                                       "reason": "Słowo o nietypowo długim przedziale; sprawdź timestamp."}
                                      for w in words if w["end"] - w["start"] > 5]}
        self.progress("Atomowy zapis i sprawdzanie TXT, SRT, VTT, JSON", 0)
        export(folder, report, execution)
        self.store.update(job["id"], status="done", stage="Gotowe", progress=100, error="")
        try:
            cleanup(work, job)
        except OSError as exc:
            # Outputs are valid. Keep evidence of cleanup failure, retry next start.
            self.store.update(job["id"], error="Wyniki gotowe; sprzątanie wymaga ponowienia: " + str(exc))

    def run(self, check=True, max_jobs=None):
        lock = WorkerLock(self.root)
        if not lock.acquire():
            return False
        heartbeat_stop = threading.Event()
        def heartbeat():
            while not heartbeat_stop.wait(5):
                self.store.control(heartbeat=now())
        thread = threading.Thread(target=heartbeat, daemon=True)
        try:
            self.store.control(state="running", pid=os.getpid(), heartbeat=now(), message="")
            self.store.recover()
            if check:
                preflight(self.config, self.root)
            # Retry only cleanup of verified completed jobs, never inference.
            for job in self.store.jobs():
                if job["status"] == "done":
                    folder = job_folder(self.root, job)
                    work = folder / "robocze"
                    leftovers = (work / "audio.f32le").exists() or any(p.suffix != ".json" for p in work.glob("download.*"))
                    if leftovers and verify_outputs(folder, job["identity"]):
                        cleanup(folder / "robocze", job)
            thread.start()
            processed = 0
            with windows_sleep_guard():
                while max_jobs is None or processed < max_jobs:
                    job = self.store.claim()
                    if job is None:
                        break
                    self.current = job
                    self.last_update = 0
                    self.store.update(job["id"], attempts=job["attempts"] + 1)
                    try:
                        self.processor(job)
                    except MediaError as exc:
                        self.store.update(job["id"], status=exc.status, error=str(exc), stage="Zatrzymano etap")
                        if exc.status == "blocked":
                            self.store.control(stop=1, message="Kolejka wstrzymana. " + str(exc))
                    except ResourceError as exc:
                        self.store.update(job["id"], status="error", error=str(exc), stage="Wymaga działania")
                        self.store.control(stop=1, message=str(exc))
                    except Exception as exc:
                        logging.exception("Błąd zadania %s", job["identity"])
                        self.store.update(job["id"], status="error", error=str(exc)[:2500], stage="Błąd")
                        if isinstance(exc, (MemoryError, OSError)):
                            self.store.control(stop=1, message=str(exc))
                    finally:
                        self.store.control(current_id=None)
                        self.current = None
                    processed += 1
        except Exception as exc:
            logging.exception("Wykonawca został wstrzymany")
            self.store.control(stop=1, message=str(exc))
        finally:
            heartbeat_stop.set()
            if thread.is_alive():
                thread.join(timeout=6)
            self.store.control(state="idle", pid=None, heartbeat=now(), current_id=None)
            lock.close()
        return True


def launch(root):
    store = Store(root)
    # Clicking Start is the only normal way to clear a stop request.
    store.start()
    if WorkerLock.busy(root):
        return None
    log_path = Path(root) / "worker.log"
    python = application_python()
    with log_path.open("ab", buffering=0) as log:
        return subprocess.Popen([str(python), str(APP / "kolejka.py"), "--root", str(root), "worker"],
            cwd=APP, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True)
