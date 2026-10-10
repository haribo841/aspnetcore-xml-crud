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
from batch.paths import local_path


def argument_parser():
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
    local.add_argument("--output", choices=("beside", "central", "custom"), default="beside")
    local.add_argument("--output-folder", type=Path, help="Folder wyników dla --output custom")
    start = commands.add_parser("start")
    start.add_argument("--kind", choices=("all", "youtube", "local"), default="all", help="Rodzaj nagrań do przetworzenia")
    start.add_argument("--ids", type=int, nargs="+", help="Wyłącznie wskazane identyfikatory wierszy SQLite")
    commands.add_parser("stop")
    commands.add_parser("status")
    commands.add_parser("retry")
    csv = commands.add_parser("csv")
    csv.add_argument("path", type=Path)
    csv.add_argument("--kind", choices=("all", "youtube", "local"), help="Filtr rodzaju nagrań w raporcie")
    return parser


def open_gui(args, _store):
    if os.name == "nt":
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    from batch.gui import Window
    local = getattr(args, "local", False) or getattr(args, "local_folder", None)
    window = Window(args.root, start_page="local" if local else None)
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


def run_worker(args, _store):
    held_lock = None
    try:
        if args.wait_for_launch:
            held_lock = WorkerLock(args.root)
            limit = time.monotonic() + 10
            while not held_lock.acquire():
                if time.monotonic() >= limit:
                    raise ResourceError("Nie udało się przejąć blokady wykonawcy.")
                time.sleep(.02)
        from batch.worker import Worker
        Worker(args.root).run(max_jobs=args.max_jobs, _held_lock=held_lock)
    finally:
        if held_lock:
            held_lock.close()


def import_catalog(args, store):
    from batch.importers import import_xlsx
    print(json.dumps(import_xlsx(store, args.path), ensure_ascii=False, indent=2))


def add_local(args, store):
    from batch.importers import import_local
    from batch.common import settings
    print(json.dumps(import_local(store, args.paths, print, config=settings(args.root),
                                 output_mode=args.output, output_root=args.output_folder or ""), ensure_ascii=False, indent=2))


def start_queue(args, _store):
    from batch.worker import launch, wait_for_launch
    launch(args.root, kind=args.kind, job_ids=args.ids)
    wait_for_launch(args.root)


def stop_queue(_args, store):
    store.control(stop=1)
    print("Dokończę bieżący film i zatrzymam kolejkę.")


def retry_queue(_args, store):
    print(f"Do ponowienia: {store.retry()}")


def queue_status(_args, store):
    from collections import Counter
    print(json.dumps({"control": store.control(), "counts": Counter(j["status"] for j in store.jobs())}, ensure_ascii=False, indent=2))


def export_report(args, store):
    store.export_csv(args.path, kind=args.kind)


def main():
    args = argument_parser().parse_args()
    args.root = local_path(args.root)
    store = Store(args.root)
    logging.basicConfig(filename=args.root / "kolejka.log", level=logging.INFO,
                        encoding="utf-8", format="%(asctime)s %(levelname)s %(message)s")
    handlers = {"gui": open_gui, "worker": run_worker, "import-xlsx": import_catalog,
                "add-local": add_local, "start": start_queue, "stop": stop_queue,
                "retry": retry_queue, "status": queue_status, "csv": export_report}
    handlers[args.command or "gui"](args, store)


if __name__ == "__main__":
    main()
