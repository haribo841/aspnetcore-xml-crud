from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .common import APP, NO_WINDOW, WorkerLock, application_python, atomic_json, job_folder, read_json, settings
from .diarization import validate_diarization
from .importers import MEDIA, import_local, import_xlsx
from .media import audio_tracks
from .result_paths import output_paths
from .store import RETRYABLE, STATUSES, Store
from .transcript_tab import TranscriptTab
from .ui_help import HELP, Tooltip, help_button, wrap_with_parent
from .worker import launch, preflight
from .youtube_access import SessionError, error_message, preferences as youtube_preferences


class Window:
    def __init__(self, root, tk_root=None):
        self.root = Path(root)
        self.store, self.config = Store(root), settings(root)
        self.window = tk_root or tk.Tk()
        self.window.title("Kolejka transkrypcji")
        self.window.geometry("1360x860")
        self.window.minsize(1220, 760)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.events = queue.Queue()
        self.visible = {}
        self.jobs = {}
        self.busy_import = False
        self.pending_filter = None
        self.setup_dialog = None
        self.youtube_dialog = None
        self.guide_dialog = None
        self.controls = {}
        self.help_status = tk.StringVar(master=self.window, value="Najedź na dowolny przycisk, aby zobaczyć opis. Pomoc działa też z klawiatury: Tab, następnie F1.")
        self.style = ttk.Style(self.window)
        if "vista" in self.style.theme_names():
            self.style.theme_use("vista")
        self.window.option_add("*Font", "{Segoe UI} 10")
        self.notebook = ttk.Notebook(self.window)
        self.notebook.pack(fill="both", expand=True)
        self.queue_page = ttk.Frame(self.notebook)
        self.processing_page = ttk.Frame(self.notebook)
        self.notebook.add(self.queue_page, text="Kolejka nagrań")
        self.notebook.add(self.processing_page, text="Obróbka transkrypcji")
        Tooltip(self.notebook, "Kolejka nagrań przetwarza audio. Obróbka transkrypcji tworzy kopie gotowych tekstów bez oznaczeń mówców.", self.help_status)
        outer = ttk.Frame(self.queue_page, padding=12)
        outer.pack(fill="both", expand=True)
        top = ttk.Frame(outer)
        top.pack(fill="x")
        for title, items in (("1. Dodaj nagrania", (
                ("import", "Importuj katalog YouTube", self.import_catalog),
                ("files", "Dodaj pliki", self.add_files), ("folder", "Dodaj folder", self.add_folder))),
                ("2. Przygotuj modele", (("speakers", "Konfiguracja mówców", self.configure_model),
                ("test", "Próba lokalna (do 2 min)", self.test_local))),
                ("Pomoc i raport", (("guide", "Co robią przyciski?", self.show_guide),
                ("csv", "Raport CSV", self.report)))):
            group = ttk.LabelFrame(top, text=title, padding=(6, 3))
            group.pack(side="left", padx=(0, 8))
            for key, label, action in items:
                self.button(group, key, label, action).pack(side="left", padx=3, pady=3)
        ttk.Label(outer, text=f"Wyniki i postęp: {self.root}").pack(anchor="w", pady=(6, 0))
        self.source_note = tk.StringVar(value="Import nie uruchamia przetwarzania. Kolejność: od najstarszych.")
        with self.store.connect() as db:
            last_import = db.execute("SELECT notes FROM imports ORDER BY id DESC LIMIT 1").fetchone()
            if last_import:
                self.source_note.set(last_import["notes"])
        ttk.Label(outer, textvariable=self.source_note, wraplength=1220).pack(anchor="w", pady=(0, 6))
        youtube_row = ttk.Frame(outer)
        youtube_row.pack(fill="x", pady=(0, 4))
        self.button(youtube_row, "youtube_access", "Dostęp YouTube", self.configure_youtube).pack(side="left", padx=(0, 10))
        self.youtube_state = tk.StringVar(value="YouTube: anonimowo")
        ttk.Label(youtube_row, textvariable=self.youtube_state).pack(side="left")
        options_row = ttk.Frame(outer)
        options_row.pack(fill="x", pady=4)
        self.uvr_enabled = tk.BooleanVar(value=self.config.get("uvr_enabled", False))
        self.keep_audio = tk.BooleanVar(value=self.config.get("keep_audio", False))
        uvr_option = ttk.Checkbutton(options_row, text="UVR: transkrybuj wydzielony wokal", variable=self.uvr_enabled,
                                    command=self.save_audio_options)
        uvr_option.pack(side="left", padx=(0, 16))
        self.add_help(uvr_option, "uvr")
        keep_option = ttk.Checkbutton(options_row, text="Zachowuj źródłowe audio", variable=self.keep_audio,
                                     command=self.save_audio_options)
        keep_option.pack(side="left", padx=(0, 16))
        self.add_help(keep_option, "keep")
        self.button(options_row, "uvr_model", "Pobierz model UVR", self.setup_uvr).pack(side="left")
        ttk.Label(options_row, text="  UVR zachowuje także wokal.flac; zmiany przed Start.").pack(side="left")
        filter_row = ttk.Frame(outer)
        filter_row.pack(fill="x", pady=6)
        self.search, self.status, self.kind = tk.StringVar(), tk.StringVar(value="Wszystkie"), tk.StringVar(value="Wszystkie")
        self.date_from, self.date_to = tk.StringVar(), tk.StringVar()
        for key, label, variable, width in (("search", "Szukaj", self.search, 26),
                                        ("date_from", "Od (RRRR-MM-DD)", self.date_from, 12),
                                        ("date_to", "Do", self.date_to, 12)):
            ttk.Label(filter_row, text=label).pack(side="left", padx=(0, 5))
            entry = ttk.Entry(filter_row, textvariable=variable, width=width)
            entry.pack(side="left", padx=(0, 12))
            self.add_help(entry, key)
        for key, label, variable, values, width in (("status", "Status", self.status, ["Wszystkie", *STATUSES.values()], 22),
                                         ("type", "Typ", self.kind, ["Wszystkie", "YouTube", "Lokalne", "Film", "Transmisja", "Shorts"], 14)):
            ttk.Label(filter_row, text=label).pack(side="left", padx=(0, 5))
            choice = ttk.Combobox(filter_row, textvariable=variable, values=values, state="readonly", width=width)
            choice.pack(side="left", padx=(0, 12))
            self.add_help(choice, key)
        for variable in (self.search, self.status, self.kind, self.date_from, self.date_to):
            variable.trace_add("write", self.schedule_filter)
        list_frame = ttk.Frame(outer)
        list_frame.pack(fill="both", expand=True)
        columns = ("enabled", "date", "type", "status", "progress", "title", "language", "audio")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="extended", height=6)
        self.add_help(self.tree, "list")
        widths = (45, 98, 88, 150, 60, 510, 62, 55)
        for column, title, width in zip(columns, ("Kolejka", "Data", "Typ", "Status", "Etap %", "Tytuł", "Język", "Audio"), widths):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=40, stretch=column == "title")
        vertical = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(list_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)
        self.tree.bind("<Double-1>", lambda event: self.open_results())
        self.tree.bind("<Control-a>", self.select_visible)
        self.tree.tag_configure("done", foreground="#25723b")
        self.tree.tag_configure("error", foreground="#ae2020")
        self.tree.tag_configure("draft", foreground="#777777")
        selection_row = ttk.Frame(outer)
        selection_row.pack(fill="x", pady=6)
        ttk.Label(selection_row, text="Wybór nagrań:").pack(side="left", padx=(0, 8))
        for key, label, action in (("select", "Zaznacz widoczne", self.select_visible),
                              ("enable", "Włącz do kolejki", lambda: self.enable(True)),
                              ("disable", "Wyłącz z kolejki", lambda: self.enable(False)),
                              ("language", "Ustaw język", self.language),
                              ("track", "Wybierz ścieżkę audio", self.audio_track)):
            self.button(selection_row, key, label, action).pack(side="left", padx=(0, 6))
        self.counts, self.current, self.detail, self.model_state = (tk.StringVar() for _ in range(4))
        ttk.Label(outer, textvariable=self.counts).pack(anchor="w", pady=4)
        ttk.Label(outer, textvariable=self.current, wraplength=1230).pack(anchor="w")
        self.progress = ttk.Progressbar(outer, maximum=100)
        self.progress.pack(fill="x", pady=6)
        self.add_help(self.progress, "progress")
        ttk.Label(outer, textvariable=self.detail, wraplength=1230).pack(anchor="w")
        ttk.Label(outer, textvariable=self.model_state, wraplength=1230).pack(anchor="w", pady=4)
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(8, 0))
        for key, label, action in (("start", "Start/Wznów", self.start),
                              ("stop", "Dokończ bieżący i zatrzymaj", self.stop),
                              ("retry", "Ponów nieudane", self.retry), ("results", "Otwórz wyniki", self.open_results),
                              ("transcript", "Otwórz TXT", self.open_transcript)):
            self.button(actions, key, label, action).pack(side="left", padx=(0, 8))
        self.selected_only = tk.BooleanVar(value=False)
        selection_option = ttk.Checkbutton(actions, text="Start tylko zaznaczonych", variable=self.selected_only)
        selection_option.pack(side="left")
        self.add_help(selection_option, "selected_only")
        ttk.Label(outer, text="Zamknięcie okna nie przerywa pracy. Zatrzymanie kończy cały bieżący film. Ctrl/Shift: wybór wielu pozycji.").pack(anchor="w", pady=(8, 0))
        help_frame = ttk.LabelFrame(outer, text="Podpowiedź", padding=(10, 5), height=80)
        help_frame.pack(fill="x", pady=(10, 0))
        help_frame.pack_propagate(False)
        self.help_label = ttk.Label(help_frame, textvariable=self.help_status, wraplength=1260,
                                   foreground="#344963", justify="left")
        self.help_label.pack(fill="both", expand=True)
        help_frame.bind("<Configure>", lambda event: self.help_label.configure(wraplength=max(300, event.width - 25)))
        for widget in outer.winfo_children():
            if isinstance(widget, ttk.Label) and int(widget.cget("wraplength") or 0):
                wrap_with_parent(widget, 24)
        self.transcript_tab = TranscriptTab(self, self.processing_page)
        self.refresh()
        self.events_after = self.window.after(250, self.consume_events)

    def add_help(self, widget, key):
        self.controls[key] = widget
        Tooltip(widget, HELP[key], self.help_status)

    def button(self, parent, key, label, action):
        button = help_button(parent, label, action, HELP[key], self.help_status)
        self.controls[key] = button
        return button

    def close(self):
        # The detached process owns computation and the sleep-prevention lease.
        if self.transcript_tab.busy:
            self.window.withdraw()
            self.transcript_tab.on_finished = self.close
            return
        self.transcript_tab.close()
        if self.setup_dialog and not self.setup_dialog.closed:
            self.setup_dialog.close()
        if self.youtube_dialog and not self.youtube_dialog.closed:
            self.youtube_dialog.close()
        # Cancel only Python callbacks we own. ttk progress bars also schedule
        # native Tcl scripts, which must be stopped by their own widgets.
        for callback in (self.pending_filter, self.refresh_after, self.events_after):
            if callback:
                self.window.after_cancel(callback)
        self.window.destroy()

    def selected(self):
        return [int(item) for item in self.tree.selection()]

    def select_visible(self, event=None):
        self.tree.selection_set(self.tree.get_children())
        return "break"

    def schedule_filter(self, *_):
        if self.pending_filter:
            self.window.after_cancel(self.pending_filter)
        self.pending_filter = self.window.after(250, self.filter_now)

    def filter_now(self):
        self.pending_filter = None
        self.render_list()

    def type_of(self, job):
        return "Lokalne" if job["kind"] == "local" else json.loads(job["source_meta"]).get("kind", "YouTube")

    def render_list(self):
        selected = set(self.tree.selection())
        wanted = []
        for job in self.jobs.values():
            if self.search.get().casefold() not in (job["title"] + " " + job["identity"]).casefold():
                continue
            if self.status.get() != "Wszystkie" and STATUSES[job["status"]] != self.status.get():
                continue
            kind = self.kind.get()
            if kind == "YouTube" and job["kind"] != "youtube":
                continue
            if kind not in {"Wszystkie", "YouTube"} and self.type_of(job) != kind:
                continue
            day = job["date"][:10]
            if (self.date_from.get() and day < self.date_from.get()) or (self.date_to.get() and day > self.date_to.get()):
                continue
            item = str(job["id"])
            wanted.append(item)
            values = ("Tak" if job["enabled"] else "", day, self.type_of(job), STATUSES[job["status"]],
                      f"{job['progress']:.0f}", job["title"], job["language"], job["audio_track"] + 1)
            if self.visible.get(item) != values:
                tag = "done" if job["status"] == "done" else "draft" if job["status"] == "draft" else "error" if job["status"] in RETRYABLE else ""
                if self.tree.exists(item):
                    self.tree.item(item, values=values, tags=(tag,))
                else:
                    self.tree.insert("", "end", iid=item, values=values, tags=(tag,))
                self.visible[item] = values
        wanted_set = set(wanted)
        for item in list(self.visible):
            if item not in wanted_set:
                self.tree.delete(item)
                del self.visible[item]
        actual = self.tree.get_children()
        if list(actual) != wanted:
            for index, item in enumerate(wanted):
                self.tree.move(item, "", index)
        self.tree.selection_set(list(selected & wanted_set))

    def refresh(self):
        rows = self.store.jobs()
        self.jobs = {job["id"]: job for job in rows}
        counts = Counter(job["status"] for job in rows)
        enabled = sum(j["status"] == "pending" and j["enabled"] for j in rows)
        self.counts.set(f"Razem: {len(rows)} | Gotowe: {counts['done']} | Oczekujące: {counts['pending']} (włączone: {enabled}) | Problemy: {sum(counts[s] for s in RETRYABLE)} | Szkice: {counts['draft']}")
        control = self.store.control()
        job = self.jobs.get(control["current_id"])
        alive = WorkerLock.busy(self.root)
        if job and alive:
            suffix = " | Zatrzymam się po tym filmie." if control["stop"] else ""
            self.current.set(f"{job['title']} | {job['stage']}{suffix}")
            self.progress["value"] = job["progress"]
        else:
            message = control["message"]
            if "not a bot" in message.casefold() or "too many requests" in message.casefold():
                message = error_message(message)
            checking = self.youtube_dialog and self.youtube_dialog.checking and not self.youtube_dialog.closed
            self.current.set(("Trwa test dostępu YouTube. " if checking else "Przygotowanie pracy..." if alive else "Kolejka zatrzymana. ") + message[:500])
            self.progress["value"] = 0
        try:
            access = youtube_preferences(self.root)
            mode = {"anonymous": "anonimowo", "browser": "sesja " + access["browser"], "file": "plik cookies"}[access["mode"]]
            self.youtube_state.set(f"YouTube: {mode} | przerwa przed filmem: {access['delay_seconds']:g} s | przy blokadzie otwórz Dostęp YouTube")
        except (SessionError, OSError):
            self.youtube_state.set("Sprawdź ustawienia w Dostęp YouTube.")
        state = read_json(self.root / "konfiguracja-modelu.json", {})
        try:
            validate_diarization(self.config)
            message = "Model mówców pobrany. "
        except Exception:
            message = "Mówcy: kliknij Konfiguracja mówców u góry, aby przygotować Community-1. "
        self.model_state.set(message + state.get("message", "")[:500])
        uvr_state = read_json(self.root / "konfiguracja-uvr.json", {})
        if uvr_state:
            self.model_state.set(self.model_state.get() + " | UVR: " + uvr_state.get("message", "")[:180])
        self.render_list()
        self.refresh_after = self.window.after(1200, self.refresh)

    def show_detail(self, event=None):
        selected = self.selected()
        if len(selected) == 1 and selected[0] in self.jobs:
            job = self.jobs[selected[0]]
            try:
                result = str(output_paths(job_folder(self.root, job))["txt"]) if job["status"] == "done" else job["source"]
                error = job["error"]
                if job["kind"] == "youtube" and error and ("ERROR:" in error or "not a bot" in error.casefold()):
                    error = error_message(error)
                self.detail.set((error or result)[:600])
            except (ValueError, OSError, TypeError):
                self.detail.set("Nie udało się odczytać nazwy wyniku. Otwórz folder nagrania.")
        else:
            self.detail.set(f"Zaznaczono: {len(selected)}")

    def background(self, function):
        if self.busy_import:
            messagebox.showinfo("Trwa operacja", "Poczekaj na zakończenie bieżącego importu.", parent=self.window)
            return
        self.busy_import = True
        def run():
            try:
                self.events.put(("result", function()))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("finished", None))
        threading.Thread(target=run, daemon=True).start()

    def consume_events(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == "finished":
                self.busy_import = False
            elif kind == "error":
                messagebox.showerror("Nie udało się wykonać operacji", str(value), parent=self.window)
            elif kind == "progress":
                self.source_note.set(str(value))
            else:
                if isinstance(value, dict):
                    self.source_note.set(value.get("note") or f"Import: dodano {value.get('added', 0)}, znaleziono {value.get('files', value.get('rows', 0))}.")
                else:
                    self.source_note.set(str(value))
        self.events_after = self.window.after(250, self.consume_events)

    def import_catalog(self):
        path = filedialog.askopenfilename(title="Katalog YouTube", filetypes=[("Arkusz Excel", "*.xlsx")], parent=self.window)
        if path:
            self.source_note.set("Importowanie katalogu...")
            self.background(lambda: import_xlsx(self.store, path))

    def add_files(self):
        paths = filedialog.askopenfilenames(title="Dodaj nagrania", filetypes=[("Audio i wideo", " ".join("*" + ext for ext in sorted(MEDIA))), ("Wszystkie", "*.*")], parent=self.window)
        if paths:
            self.background(lambda: import_local(self.store, paths, lambda message: self.events.put(("progress", message))))

    def add_folder(self):
        path = filedialog.askdirectory(title="Folder z nagraniami (również podfoldery)", parent=self.window)
        if path:
            self.background(lambda: import_local(self.store, [path], lambda message: self.events.put(("progress", message))))

    def enable(self, enabled):
        self.store.configure(self.selected(), enabled=int(enabled))

    def language(self):
        if not self.selected():
            return
        value = simpledialog.askstring("Język zaznaczonych", "Kod języka, np. pl, en, de lub auto. Nagrania gotowe i bieżące pozostają bez zmian.", initialvalue="auto", parent=self.window)
        if value:
            value = value.strip().lower()
            import re
            if value != "auto" and not re.fullmatch(r"[a-z]{2,3}", value):
                messagebox.showerror("Język", "Podaj kod języka lub auto.", parent=self.window)
                return
            self.store.configure(self.selected(), language=value)

    def audio_track(self):
        ids = self.selected()
        if len(ids) != 1:
            messagebox.showinfo("Ścieżka audio", "Zaznacz jeden plik lokalny.", parent=self.window)
            return
        job = self.jobs[ids[0]]
        if job["kind"] != "local":
            messagebox.showinfo("Ścieżka audio", "Dla YouTube pobierany jest najlepszy dostępny strumień audio.", parent=self.window)
            return
        try:
            tracks = audio_tracks(job["source"], self.config)
            descriptions = [f"{index + 1}: {s.get('codec_name')} | {s.get('channels')} kanałów | {s.get('tags', {}).get('language', '')} {s.get('tags', {}).get('title', '')}" for index, s in enumerate(tracks)]
            value = simpledialog.askinteger("Ścieżka audio", "\n".join(descriptions) + "\n\nNumer ścieżki:", minvalue=1, maxvalue=len(tracks), initialvalue=job["audio_track"] + 1, parent=self.window)
            if value:
                self.store.configure(ids, audio_track=value - 1)
        except Exception as exc:
            messagebox.showerror("Ścieżka audio", str(exc), parent=self.window)

    def start(self):
        if self.youtube_dialog and self.youtube_dialog.running:
            if not self.youtube_dialog.closed:
                self.youtube_dialog.dialog.lift()
            self.help_status.set("Poczekaj na zakończenie lub anulowanie testu YouTube, a następnie naciśnij Start/Wznów.")
            return
        try:
            self.config = settings(self.root)
            if self.config["diarization"]:
                try:
                    validate_diarization(self.config)
                except Exception:
                    self.configure_model()
                    return
            preflight(self.config, self.root)
            if self.config["diarization"]:
                test = read_json(self.root / "pierwsza-proba.json", {})
                if not test.get("passed") or test.get("whisper_revision") != self.config["model_revision"] or test.get("diar_revision") != self.config["diar_revision"]:
                    self.configure_model()
                    self.setup_dialog.tabs.select(2)
                    self.setup_dialog.progress_message.set("Przed pierwszym Start wykonaj krótką próbę przyciskiem poniżej. Kolejka filmów pozostanie zatrzymana.")
                    return
            if self.selected_only.get():
                ids = self.selected()
                if not ids:
                    messagebox.showinfo("Start", "Zaznacz materiały do przetworzenia.", parent=self.window)
                    return
                self.store.configure(list(self.jobs), enabled=0)
                self.store.configure(ids, enabled=1)
            launch(self.root)
        except Exception as exc:
            messagebox.showerror("Kolejka nie została uruchomiona", str(exc), parent=self.window)

    def stop(self):
        self.store.control(stop=1)

    def retry(self):
        count = self.store.retry(self.selected() or None)
        self.source_note.set(f"Przywrócono do oczekujących: {count}. Naciśnij Start/Wznów.")

    def open_results(self):
        ids = self.selected()
        path = job_folder(self.root, self.jobs[ids[0]]) if len(ids) == 1 else self.root / "wyniki"
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def open_transcript(self):
        ids = self.selected()
        if len(ids) != 1 or self.jobs[ids[0]]["status"] != "done":
            messagebox.showinfo("Transkrypcja", "Zaznacz jedno nagranie ze statusem Gotowe.", parent=self.window)
            return
        try:
            path = output_paths(job_folder(self.root, self.jobs[ids[0]]))["txt"]
            os.startfile(path)
        except (ValueError, OSError, TypeError) as exc:
            messagebox.showerror("Nie udało się otworzyć transkrypcji", str(exc), parent=self.window)

    def report(self):
        path = filedialog.asksaveasfilename(title="Raport do Excela", defaultextension=".csv", initialfile="postep.csv", filetypes=[("CSV", "*.csv")], parent=self.window)
        if path:
            self.background(lambda: (self.store.export_csv(path), f"Zapisano raport: {path}")[1])

    def setup_process(self, request):
        if WorkerLock.busy(self.root / "konfiguracja"):
            messagebox.showinfo("Konfiguracja", "Pobieranie lub próba już trwa.", parent=self.window)
            return False
        try:
            atomic_json(self.root / "konfiguracja-modelu.json", {"state": "running", "phase": "starting",
                        "message": "Uruchamianie konfiguracji. Możesz pozostawić to okno otwarte."})
            with (self.root / "konfiguracja.log").open("ab") as log:
                process = subprocess.Popen([application_python(), "-m", "batch.setup_model", str(self.root)],
                    cwd=APP, stdin=subprocess.PIPE, stdout=log, stderr=log,
                    creationflags=NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0),
                    env=dict(os.environ, PYTHONIOENCODING="utf-8"))
                with process.stdin:
                    process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        except (OSError, ValueError):
            # Never include request contents, command-line tokens or HTTP traces.
            message = "Nie udało się uruchomić konfiguracji. Sprawdź dostęp do folderu wyników i środowisko Python aplikacji, a potem spróbuj ponownie."
            atomic_json(self.root / "konfiguracja-modelu.json", {"state": "error", "message": message})
            messagebox.showerror("Konfiguracja", message, parent=self.window)
            return False
        self.model_state.set("Uruchamianie konfiguracji...")
        return True

    def save_audio_options(self):
        if WorkerLock.busy(self.root):
            messagebox.showinfo("Ustawienia sesji", "Najpierw dokończ bieżący film i zatrzymaj kolejkę. Opcje audio zmienisz przed następnym Start.", parent=self.window)
            self.uvr_enabled.set(self.config.get("uvr_enabled", False))
            self.keep_audio.set(self.config.get("keep_audio", False))
            return
        if self.uvr_enabled.get():
            self.keep_audio.set(True)
        self.config = settings(self.root)
        self.config.update(uvr_enabled=self.uvr_enabled.get(), keep_audio=self.keep_audio.get())
        atomic_json(self.root / "ustawienia.json", self.config)

    def setup_uvr(self):
        if WorkerLock.busy(Path(self.config["uvr_model_dir"])):
            return
        python = Path(self.config["uvr_python"])
        if not python.is_file():
            messagebox.showinfo("UVR", "Uruchom Instaluj-UVR.ps1, a następnie pobierz model.", parent=self.window)
            return
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        env["PATH"] = str(Path(self.config["ffmpeg"]).parent) + os.pathsep + env.get("PATH", "")
        with (self.root / "konfiguracja-uvr.log").open("ab") as log:
            subprocess.Popen([str(python), "-m", "batch.uvr_child", "--setup", str(self.root)], cwd=APP,
                stdout=log, stderr=log, stdin=subprocess.DEVNULL, env=env,
                creationflags=NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0))

    def test_local(self):
        try:
            validate_diarization(self.config)
        except Exception:
            self.configure_model()
            return
        path = filedialog.askopenfilename(title="Krótka próba (do 2 minut)", parent=self.window)
        if path:
            if self.setup_process({"test_only": True, "source": path}):
                self.configure_model()
                self.setup_dialog.tabs.select(2)

    def configure_model(self):
        from .setup_wizard import SetupWizard
        if self.setup_dialog and not self.setup_dialog.closed:
            self.setup_dialog.dialog.deiconify()
            self.setup_dialog.dialog.lift()
            return
        self.setup_dialog = SetupWizard(self)

    def configure_youtube(self):
        from .youtube_dialog import YouTubeDialog
        if self.youtube_dialog and not self.youtube_dialog.closed:
            self.youtube_dialog.dialog.deiconify()
            self.youtube_dialog.dialog.lift()
            return
        self.youtube_dialog = YouTubeDialog(self)

    def show_guide(self):
        if self.guide_dialog and self.guide_dialog.winfo_exists():
            self.guide_dialog.lift()
            return
        dialog = tk.Toplevel(self.window)
        self.guide_dialog = dialog
        dialog.title("Jak korzystać z kolejki")
        dialog.geometry("820x650")
        dialog.minsize(640, 480)
        dialog.transient(self.window)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        heading = ttk.Label(frame, text="Pierwsza sesja: modele → krótka próba → wybór nagrań → Start", font=("Segoe UI", 12, "bold"), wraplength=770)
        heading.pack(anchor="w", pady=(0, 10))
        wrap_with_parent(heading, 32)
        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True)
        content = tk.Text(body, wrap="word", padx=12, pady=10, font=("Segoe UI", 10))
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=content.yview)
        content.configure(yscrollcommand=scrollbar.set)
        content.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        content.tag_configure("heading", font=("Segoe UI", 11, "bold"), spacing1=12, spacing3=4)
        for heading, keys in (("Przygotowanie", ("speakers", "test", "youtube_access", "import", "files", "folder")),
                              ("Wybór i ustawienia nagrań", ("search", "select", "enable", "disable", "language", "track", "uvr", "keep", "uvr_model")),
                              ("Praca i wyniki", ("start", "selected_only", "stop", "retry", "results", "transcript", "csv"))):
            content.insert("end", heading + "\n", "heading")
            for key in keys:
                widget = self.controls[key]
                label = widget.cget("text") if "text" in widget.keys() else "Szukaj i filtry"
                content.insert("end", label + "\n", "heading")
                content.insert("end", HELP[key] + "\n")
        content.insert("end", "\nZamknięcie okna nie przerywa rozpoczętej pracy. Najedź na element okna lub użyj Tab i F1, aby wyświetlić podpowiedź.\n")
        content.insert("end", "Obróbka transkrypcji\n", "heading")
        content.insert("end", "Druga karta usuwa etykiety mówców z gotowych TXT, SRT i VTT. Dodaj pliki lub wybierz Wszystkie gotowe, sprawdź podgląd i zapisz kopie. Timestampy oraz wypowiedzi pozostają bez zmian. Domyślny folder kopii to obrobione; oryginały pozostają zachowane.\n")
        content.configure(state="disabled")
        Tooltip(content, "Przewijaj kółkiem myszy lub klawiszami Page Up / Page Down. Tekst możesz zaznaczyć i skopiować.")
        help_button(frame, "Zamknij pomoc", dialog.destroy, "Wraca do kolejki bez zmiany jej stanu.").pack(anchor="e", pady=(10, 0))

    def run(self):
        self.window.mainloop()
