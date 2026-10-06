"""One-time gated model download. Token is read from stdin, never saved."""
from __future__ import annotations

import json
from datetime import datetime
import os
from pathlib import Path
import sys

os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"
os.environ["OTEL_SDK_DISABLED"] = "true"

from .common import APP, WorkerLock, atomic_json, digest, settings
from .diarization import validate_diarization
from .exporter import verify_outputs
from .importers import import_local
from .hf_access import MODEL_ID, access_error, check_access, redact
from .media import probe
from .store import Store, now
from .worker import Worker


class AccessProblem(RuntimeError):
    def __init__(self, result):
        super().__init__(result["message"])
        self.result = result


def write_state(root, state, message, phase="", **extra):
    atomic_json(Path(root) / "konfiguracja-modelu.json",
                {"state": state, "phase": phase, "message": redact(message), "at": now(), **extra})


def smoke_test(root, source=None):
    root = Path(root)
    config = settings(root)
    if not source:
        raise ValueError("Wybierz krótkie nagranie przyciskiem Próba lokalna (do 2 minut).")
    source = Path(source)
    if not source.is_file():
        raise ValueError("Wybierz krótkie nagranie przyciskiem Próba lokalna (do 2 minut).")
    info = probe(source, config)
    duration = float(info.get("format", {}).get("duration", 0))
    if not 0 < duration <= 120:
        raise ValueError("Do pierwszej próby wybierz nagranie nie dłuższe niż 2 minuty.")
    # Every smoke test executes the current models, even if an earlier sample
    # with the same content has already passed under different settings.
    test_root = root / "proba" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    test_root.mkdir(parents=True, exist_ok=True)
    config["diarization"] = True
    atomic_json(test_root / "ustawienia.json", config)
    store = Store(test_root)
    imported = import_local(store, [source])
    wanted = "local:" + digest(source)
    # A previous failed sample must not be chosen in place of the new one.
    for job in store.jobs():
        store.configure([job["id"]], enabled=int(job["identity"] == wanted))
    store.retry()
    store.start()
    Worker(test_root).run(max_jobs=1)
    job = next(j for j in store.jobs() if j["identity"] == wanted)
    passed = job["status"] == "done" and verify_outputs(test_root / "wyniki" / job["folder"], wanted)
    report = {"passed": passed, "at": now(), "source": str(source), "identity": wanted,
              "whisper_revision": config["model_revision"], "diar_revision": config["diar_revision"],
              "results": str(test_root / "wyniki" / job["folder"]), "error": job["error"] or store.control()["message"]}
    atomic_json(root / "pierwsza-proba.json", report)
    if not passed:
        raise RuntimeError("Próba całego procesu nie powiodła się: " + report["error"])
    return report


def configure(root, token):
    from huggingface_hub import snapshot_download
    root = Path(root)
    config = settings(root)
    destination = Path(config["diar_model"])
    write_state(root, "running", "Sprawdzanie tokenu i dostępu do Community-1…", "access")
    access = check_access(token, config["diar_revision"])
    if not access["ok"]:
        raise AccessProblem(access)
    write_state(root, "running", "Dostęp potwierdzony. Pobieranie Community-1. Przy wolnym łączu może potrwać kilka minut; okno można zamknąć.", "download")
    # token is passed directly to the request; huggingface_hub.login is never used.
    snapshot_download(repo_id=MODEL_ID, revision=config["diar_revision"],
                      local_dir=destination, token=token, max_workers=2)
    write_state(root, "running", "Model pobrany. Sprawdzanie kompletności plików…", "verify")
    validate_diarization(config)
    files = {str(p.relative_to(destination)): digest(p) for p in destination.rglob("*")
             if p.is_file() and ".cache" not in p.parts and p.name != "pobrano.json"}
    atomic_json(destination / "pobrano.json", {"repository": MODEL_ID,
                "revision": config["diar_revision"], "files": files, "at": now()})
    write_state(root, "ready", "Model mówców pobrany i sprawdzony. Kliknij Uruchom krótką próbę i wybierz własne nagranie do 2 minut. Przed próbą pobierz także model Whisper. Kolejka filmów czeka na Twój Start.", "download_complete")


def main():
    root = Path(sys.argv[1]).resolve()
    lock = WorkerLock(root / "konfiguracja")
    if not lock.acquire():
        return 0
    token = ""
    try:
        request = json.loads(sys.stdin.readline())
        if request.get("test_only"):
            write_state(root, "running", "Trwa krótka próba lokalna: Whisper, mówcy i zapis wyników. Pierwsze uruchomienie modeli może potrwać kilka minut.", "test")
            report = smoke_test(root, request.get("source"))
            write_state(root, "ready", "Próba zakończona. Otwórz wyniki i sprawdź tekst. Kolejka filmów czeka na Twój Start.", "complete", test_results=report["results"])
        else:
            token = request.pop("token", "").strip()
            if not token:
                raise ValueError("Wpisz token Hugging Face z dostępem do pobrania modelu.")
            configure(root, token)
        return 0
    except Exception as exc:
        result = exc.result if isinstance(exc, AccessProblem) else access_error(exc, token)
        write_state(root, "error", redact(result["message"], token), code=result["code"])
        return 1
    finally:
        token = ""
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
