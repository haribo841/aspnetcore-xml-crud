"""GUI and maintenance CLI for the persistent transcription queue."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import time

os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"

from batch.common import DEFAULT_ROOT, ResourceError, WorkerLock
from batch.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest="command")
    gui = commands.add_parser("gui")
    gui.add_argument("--setup", action="store_true", help="Otwórz kreator modelu mówców")
    gui.add_argument("--transcripts", action="store_true", help="Otwórz obróbkę gotowych transkrypcji")
    gui.add_argument("--youtube", action="store_true", help="Otwórz konfigurację dostępu YouTube")
    gui.add_argument("--local", action="store_true", help="Otwórz kolejkę nagrań lokalnych")
    gui.add_argument("--local-folder", type=Path, help="Wypełnij folder w kolejce lokalnej; bez importu i Start")
    work = commands.add_parser("worker")
    work.add_argument("--max-jobs", type=int)
    work.add_argument("--wait-for-launch", action="store_true", help=argparse.SUPPRESS)
    catalog = commands.add_parser("import-xlsx")
    catalog.add_argument("path", type=Path)
    local = commands.add_parser("add-local")
    local.add_argument("paths", type=Path, nargs="+")
    start = commands.add_parser("start")
    start.add_argument("--kind", choices=("all", "youtube", "local"), default="all", help="Rodzaj nagrań do przetworzenia")
    start.add_argument("--ids", type=int, nargs="+", help="Wyłącznie wskazane identyfikatory wierszy SQLite")
    commands.add_parser("stop")
    commands.add_parser("status")
    commands.add_parser("retry")
    csv = commands.add_parser("csv")
    csv.add_argument("path", type=Path)
    csv.add_argument("--kind", choices=("all", "youtube", "local"), help="Filtr rodzaju nagrań w raporcie")
    args = parser.parse_args()
    args.root = args.root.resolve()
    store = Store(args.root)
    logging.basicConfig(filename=args.root / "kolejka.log", level=logging.INFO,
                        encoding="utf-8", format="%(asctime)s %(levelname)s %(message)s")
    if args.command in (None, "gui"):
        if os.name == "nt":
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        from batch.gui import Window
        window = Window(args.root)
        if getattr(args, "setup", False):
            window.window.after(250, window.configure_model)
        if getattr(args, "youtube", False):
            window.window.after(250, window.configure_youtube)
        if getattr(args, "transcripts", False):
            window.transcript_tab.from_queue(False)
            window.notebook.select(window.processing_page)
        if getattr(args, "local", False) or getattr(args, "local_folder", None):
            if args.local_folder:
                window.local_tab.set_folder(args.local_folder)
            window.notebook.select(window.local_page)
        window.run()
    elif args.command == "worker":
        held_lock = None
        try:
            if args.wait_for_launch:
                held_lock = WorkerLock(args.root)
                limit = time.monotonic() + 10
                while not held_lock.acquire():
                    if time.monotonic() >= limit:
                        raise ResourceError("Nie udało się przejąć blokady wykonawcy.")
                    time.sleep(.02)
            # Acquire the handed-off lock before importing model backends. The
            # parent GUI can then finish its startup handshake immediately.
            from batch.worker import Worker
            Worker(args.root).run(max_jobs=args.max_jobs, _held_lock=held_lock)
        finally:
            if held_lock:
                held_lock.close()
    elif args.command == "import-xlsx":
        from batch.importers import import_xlsx
        print(json.dumps(import_xlsx(store, args.path), ensure_ascii=False, indent=2))
    elif args.command == "add-local":
        from batch.importers import import_local
        print(json.dumps(import_local(store, args.paths, print), ensure_ascii=False, indent=2))
    elif args.command == "start":
        from batch.worker import launch, wait_for_launch
        launch(args.root, kind=args.kind, job_ids=args.ids)
        wait_for_launch(args.root)
    elif args.command == "stop":
        store.control(stop=1)
        print("Dokończę bieżący film i zatrzymam kolejkę.")
    elif args.command == "retry":
        print(f"Do ponowienia: {store.retry()}")
    elif args.command == "status":
        from collections import Counter
        print(json.dumps({"control": store.control(), "counts": Counter(j["status"] for j in store.jobs())}, ensure_ascii=False, indent=2))
    elif args.command == "csv":
        store.export_csv(args.path, kind=args.kind)


if __name__ == "__main__":
    main()
