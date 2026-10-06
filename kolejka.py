"""GUI and maintenance CLI for the persistent transcription queue."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"

from batch.common import DEFAULT_ROOT
from batch.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest="command")
    gui = commands.add_parser("gui")
    gui.add_argument("--setup", action="store_true", help="Otwórz kreator modelu mówców")
    gui.add_argument("--transcripts", action="store_true", help="Otwórz obróbkę gotowych transkrypcji")
    gui.add_argument("--youtube", action="store_true", help="Otwórz konfigurację dostępu YouTube")
    work = commands.add_parser("worker")
    work.add_argument("--max-jobs", type=int)
    catalog = commands.add_parser("import-xlsx")
    catalog.add_argument("path", type=Path)
    local = commands.add_parser("add-local")
    local.add_argument("paths", type=Path, nargs="+")
    commands.add_parser("start")
    commands.add_parser("stop")
    commands.add_parser("status")
    commands.add_parser("retry")
    csv = commands.add_parser("csv")
    csv.add_argument("path", type=Path)
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
        window.run()
    elif args.command == "worker":
        from batch.worker import Worker
        Worker(args.root).run(max_jobs=args.max_jobs)
    elif args.command == "import-xlsx":
        from batch.importers import import_xlsx
        print(json.dumps(import_xlsx(store, args.path), ensure_ascii=False, indent=2))
    elif args.command == "add-local":
        from batch.importers import import_local
        print(json.dumps(import_local(store, args.paths, print), ensure_ascii=False, indent=2))
    elif args.command == "start":
        from batch.worker import launch
        launch(args.root)
    elif args.command == "stop":
        store.control(stop=1)
        print("Dokończę bieżący film i zatrzymam kolejkę.")
    elif args.command == "retry":
        print(f"Do ponowienia: {store.retry()}")
    elif args.command == "status":
        from collections import Counter
        print(json.dumps({"control": store.control(), "counts": Counter(j["status"] for j in store.jobs())}, ensure_ascii=False, indent=2))
    elif args.command == "csv":
        store.export_csv(args.path)


if __name__ == "__main__":
    main()
