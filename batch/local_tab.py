"""Local recordings view sharing checkpoints and one worker with YouTube."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .common import atomic_json, read_json
from .importers import MEDIA, import_local
from .store import RETRYABLE, STATUSES
from .ui_help import HELP, Tooltip, help_button, status_tags, sync_tree, wrap_with_parent


def folder_media(folder, recursive=True, m4a_only=False):
    path = Path(folder).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Wskaż folder z nagraniami.")
    extensions = {".m4a"} if m4a_only else MEDIA
    entries = path.rglob("*") if recursive else path.iterdir()
    return sorted(p for p in entries if p.is_file() and p.suffix.lower() in extensions)


class LocalQueueTab:
    def __init__(self, app, parent):
        self.app, self.parent = app, parent
        self.visible = {}
        self.filter_after = None
        state = read_json(app.root / "lokalne-ui.json", {})
        self.folder = tk.StringVar(master=parent, value=state.get("folder", ""))
        self.recursive = tk.BooleanVar(master=parent, value=state.get("recursive", True))
        self.m4a_only = tk.BooleanVar(master=parent, value=state.get("m4a_only", False))
        self.search = tk.StringVar(master=parent)
        self.status = tk.StringVar(master=parent, value="Wszystkie")
        self.selected_only = tk.BooleanVar(master=parent, value=False)
        self.note = tk.StringVar(master=parent, value="Dodaj folder lub pliki. Import nie uruchamia transkrypcji.")
        self.counts, self.current, self.detail = (tk.StringVar(master=parent) for _ in range(3))
        self.hint = tk.StringVar(master=parent, value="Start obejmuje tylko pliki lokalne. Oryginały i gotowe wyniki pozostają zachowane.")
        self.controls = {}
        frame = ttk.Frame(parent, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Transkrypcja kolejki lokalnej", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        intro = ttk.Label(frame, text="Audio i wideo z dysku, bez pobierania z YouTube. Jeden wykonawca obsługuje wybraną kolejkę; Start nie przełącza trwającej sesji.", wraplength=1200)
        intro.pack(anchor="w", pady=(3, 8))
        wrap_with_parent(intro, 24)
        source = ttk.LabelFrame(frame, text="1. Dodaj nagrania", padding=(8, 5))
        source.pack(fill="x")
        row = ttk.Frame(source)
        row.pack(fill="x")
        ttk.Label(row, text="Folder:").pack(side="left", padx=(0, 8))
        entry = ttk.Entry(row, textvariable=self.folder)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.help(entry, "folder_path", "Wpisz lub wybierz folder źródłowy. Sam wybór folderu nie importuje plików ani nie rozpoczyna pracy.")
        self.button(row, "browse", "Wybierz folder", self.choose_folder,
                    "Wybiera folder źródłowy. Następnie kliknij Dodaj folder do kolejki.").pack(side="left", padx=(0, 6))
        self.button(row, "import_folder", "Dodaj folder do kolejki", self.add_folder,
                    "Dodaje obsługiwane nagrania z wskazanego folderu. Kopie o identycznej zawartości i wcześniej dodane pliki są pomijane. Nie uruchamia transkrypcji.").pack(side="left", padx=(0, 6))
        self.button(row, "files", "Dodaj pliki", self.add_files, HELP["files"]).pack(side="left")
        row = ttk.Frame(source)
        row.pack(fill="x", pady=(5, 0))
        for key, label, variable, hint in (
                ("recursive", "Uwzględnij podfoldery", self.recursive, "Włącz, aby import obejmował również nagrania z podfolderów."),
                ("m4a_only", "Importuj tylko M4A", self.m4a_only, "Ogranicza import folderu do plików .m4a. Przy wyłączonej opcji obsługiwane są także inne pliki audio i wideo.")):
            widget = ttk.Checkbutton(row, text=label, variable=variable, command=self.save_view)
            widget.pack(side="left", padx=(0, 16))
            self.help(widget, key, hint)
        self.button(row, "speakers", "Konfiguracja mówców", app.configure_model, HELP["speakers"]).pack(side="left", padx=(0, 6))
        self.button(row, "test", "Próba lokalna (do 2 min)", app.test_local, HELP["test"]).pack(side="left")
        note = ttk.Label(frame, textvariable=self.note, wraplength=1200)
        note.pack(anchor="w", pady=5)
        wrap_with_parent(note, 24)
        options = ttk.Frame(frame)
        options.pack(fill="x", pady=(0, 5))
        for key, label, variable in (("uvr", "UVR: transkrybuj wydzielony wokal", app.uvr_enabled),
                                    ("keep", "Zachowuj źródłowe audio", app.keep_audio)):
            widget = ttk.Checkbutton(options, text=label, variable=variable, command=app.save_audio_options)
            widget.pack(side="left", padx=(0, 16))
            self.help(widget, key, HELP[key] + " Ustawienia audio są wspólne dla obu kolejek i obowiązują przy kolejnym Start.")
        self.button(options, "uvr_model", "Pobierz model UVR", app.setup_uvr, HELP["uvr_model"]).pack(side="left")
        filters = ttk.Frame(frame)
        filters.pack(fill="x", pady=(0, 5))
        ttk.Label(filters, text="Szukaj:").pack(side="left", padx=(0, 6))
        search = ttk.Entry(filters, textvariable=self.search, width=45)
        search.pack(side="left", padx=(0, 12))
        self.help(search, "search", "Filtruje lokalną listę według tytułu lub ścieżki źródła. Sam filtr nie ogranicza zakresu Start.")
        ttk.Label(filters, text="Status:").pack(side="left", padx=(0, 6))
        status = ttk.Combobox(filters, textvariable=self.status, values=["Wszystkie", *STATUSES.values()], state="readonly", width=22)
        status.pack(side="left", padx=(0, 12))
        self.help(status, "status", HELP["status"])
        self.button(filters, "csv", "Raport lokalnej kolejki CSV", lambda: app.report(kind="local"),
                    "Zapisuje do CSV wyłącznie stan lokalnych nagrań. Nie zmienia plików źródłowych ani kolejki YouTube.").pack(side="left")
        self.search.trace_add("write", self.schedule_filter)
        self.status.trace_add("write", self.schedule_filter)
        listing = ttk.Frame(frame)
        listing.pack(fill="both", expand=True)
        columns = ("enabled", "status", "stage", "progress", "title", "language", "audio")
        self.tree = ttk.Treeview(listing, columns=columns, show="headings", selectmode="extended", height=6)
        for column, title, width in zip(columns, ("Kolejka", "Status", "Etap", "Etap %", "Nagranie", "Język", "Audio"), (60, 135, 220, 60, 480, 65, 55)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, minwidth=45, stretch=column == "title")
        vertical = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(listing, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        listing.rowconfigure(0, weight=1)
        listing.columnconfigure(0, weight=1)
        self.tree.tag_configure("done", foreground="#25723b")
        self.tree.tag_configure("error", foreground="#ae2020")
        self.tree.bind("<<TreeviewSelect>>", self.show_detail)
        self.tree.bind("<Control-a>", self.select_visible)
        self.tree.bind("<Double-1>", lambda event: app.open_results(self.selected()))
        self.help(self.tree, "list", HELP["list"])
        selection = ttk.Frame(frame)
        selection.pack(fill="x", pady=5)
        for key, text, command in (
                ("select", "Zaznacz widoczne", self.select_visible),
                ("enable", "Włącz do kolejki", lambda: app.enable(True, self.selected())),
                ("disable", "Wyłącz z kolejki", lambda: app.enable(False, self.selected())),
                ("language", "Ustaw język", lambda: app.language(self.selected())),
                ("track", "Wybierz ścieżkę audio", lambda: app.audio_track(self.selected()))):
            self.button(selection, key, text, command, HELP[key]).pack(side="left", padx=(0, 6))
        for variable in (self.counts, self.current, self.detail, app.model_state):
            label = ttk.Label(frame, textvariable=variable, wraplength=1200)
            label.pack(anchor="w", pady=(0, 3))
            wrap_with_parent(label, 24)
        self.progress = ttk.Progressbar(frame, maximum=100)
        self.progress.pack(fill="x", pady=(0, 6))
        self.help(self.progress, "progress", HELP["progress"])
        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        for key, text, command, hint in (
                ("start", "Start/Wznów lokalne", self.start, "Przetwarza tylko włączone lokalne nagrania, od najstarszych. Gotowe wyniki są pomijane; kolejka YouTube nie ruszy."),
                ("stop", "Dokończ bieżący i zatrzymaj", lambda: app.stop(kind="local"), "Kończy całe bieżące lokalne nagranie i zapisuje wyniki, potem zatrzymuje sesję. Następny plik nie ruszy."),
                ("retry", "Ponów nieudane lokalne", lambda: app.retry(kind="local", ids=self.selected()), "Przywraca nieudane lokalne nagrania do oczekujących. Działa na zaznaczonych, a bez zaznaczenia na wszystkich lokalnych. YouTube pozostaje bez zmian."),
                ("results", "Otwórz wyniki", lambda: app.open_results(self.selected()), HELP["results"]),
                ("transcript", "Otwórz TXT", lambda: app.open_transcript(self.selected()), HELP["transcript"])):
            self.button(actions, key, text, command, hint).pack(side="left", padx=(0, 6))
        selected = ttk.Checkbutton(actions, text="Start tylko zaznaczonych", variable=self.selected_only)
        selected.pack(side="left")
        self.help(selected, "selected_only", "Ogranicza tę sesję do zaznaczonych lokalnych nagrań. Nie wyłącza innych plików w bazie. Przy kolejnym Start możesz wrócić do całej lokalnej kolejki.")
        help_frame = ttk.LabelFrame(frame, text="Podpowiedź", padding=(8, 5))
        help_frame.pack(fill="x", pady=(8, 0))
        hint = ttk.Label(help_frame, textvariable=self.hint, wraplength=1200)
        hint.pack(fill="x")
        wrap_with_parent(hint, 24)

    def help(self, widget, key, hint):
        self.controls[key] = widget
        Tooltip(widget, hint, self.hint)

    def button(self, parent, key, text, command, hint):
        button = help_button(parent, text, command, hint, self.hint)
        self.controls[key] = button
        return button

    def save_view(self):
        atomic_json(self.app.root / "lokalne-ui.json", {"folder": self.folder.get(),
                    "recursive": self.recursive.get(), "m4a_only": self.m4a_only.get()})

    def set_folder(self, path):
        self.folder.set(str(path))
        self.save_view()

    def choose_folder(self):
        initial = self.folder.get()
        path = filedialog.askdirectory(title="Folder lokalnych nagrań", parent=self.app.window,
                    initialdir=initial if initial and Path(initial).is_dir() else None)
        if path:
            self.set_folder(path)

    def add_folder(self):
        value = self.folder.get().strip()
        if not value:
            self.choose_folder()
            value = self.folder.get().strip()
        if not value:
            return
        recursive, m4a = self.recursive.get(), self.m4a_only.get()
        self.save_view()
        self.note.set("Wyszukiwanie i identyfikacja nagrań...")
        self.import_paths(lambda: folder_media(value, recursive, m4a))

    def add_files(self):
        paths = filedialog.askopenfilenames(parent=self.app.window, title="Lokalne nagrania do transkrypcji",
                    filetypes=[("Audio i wideo", " ".join("*" + ext for ext in sorted(MEDIA))), ("M4A", "*.m4a")])
        if paths:
            self.import_paths(lambda: paths)

    def import_paths(self, collect):
        def run():
            result = import_local(self.app.store, collect(), lambda value: self.app.events.put(("progress", value, self.note)))
            result["note"] = (f"Znaleziono: {result['files']} | Dodano: {result['added']} | "
                              f"Kopie lub już w kolejce: {result['files'] - result['added']}. Import zakończony; naciśnij Start/Wznów lokalne.")
            return result
        self.app.background(run, note=self.note)

    def selected(self):
        return [int(item) for item in self.tree.selection()]

    def select_visible(self, event=None):
        self.tree.selection_set(self.tree.get_children())
        return "break"

    def schedule_filter(self, *_):
        if self.filter_after:
            self.parent.after_cancel(self.filter_after)
        self.filter_after = self.parent.after(200, self.filter_now)

    def filter_now(self):
        self.filter_after = None
        self.render()

    def render(self):
        rows = [job for job in self.app.jobs.values() if job["kind"] == "local"]
        visible_rows = []
        for job in rows:
            if self.search.get().casefold() not in (job["title"] + " " + job["source"]).casefold():
                continue
            if self.status.get() != "Wszystkie" and STATUSES[job["status"]] != self.status.get():
                continue
            item = str(job["id"])
            values = ("Tak" if job["enabled"] else "", STATUSES[job["status"]], job["stage"],
                      f"{job['progress']:.0f}", job["title"], job["language"], job["audio_track"] + 1)
            visible_rows.append((item, values, status_tags(job["status"])))
        sync_tree(self.tree, self.visible, visible_rows)
        self.show_detail()
        counts = Counter(job["status"] for job in rows)
        pending = sum(job["status"] == "pending" and job["enabled"] for job in rows)
        self.counts.set(f"Lokalne: {len(rows)} | Gotowe: {counts['done']} | Oczekujące: {counts['pending']} (włączone: {pending}) | Problemy: {sum(counts[s] for s in RETRYABLE)}")

    def refresh(self, control, alive):
        self.render()
        job = self.app.jobs.get(control["current_id"])
        scope = job["kind"] if job else control.get("scope_kind", "all")
        self.show_current(control, alive, job, scope)
        self.controls["start"].state(["disabled"] if alive or self.app.busy_import else ["!disabled"])
        self.controls["stop"].state(["!disabled"] if alive and scope == "local" else ["disabled"])

    def show_current(self, control, alive, job, scope):
        if alive and scope == "local":
            suffix = " | Zatrzymam się po tym nagraniu." if control["stop"] else ""
            self.current.set((f"{job['title']} | {job['stage']}" if job else "Przygotowanie lokalnej sesji...") + suffix)
            self.progress["value"] = job["progress"] if job else 0
        elif alive:
            self.current.set("Trwa inna sesja. Dokończ bieżący i zatrzymaj ją w karcie YouTube, potem uruchom kolejkę lokalną.")
            self.progress["value"] = 0
        else:
            message = control["message"][:500] if scope == "local" else ""
            self.current.set("Kolejka lokalna zatrzymana. " + message)
            self.progress["value"] = 0

    def show_detail(self, event=None):
        ids = self.selected()
        if len(ids) == 1 and ids[0] in self.app.jobs:
            job = self.app.jobs[ids[0]]
            self.detail.set((job["error"] or job["source"])[:600])
        else:
            self.detail.set(f"Zaznaczono: {len(ids)} | Wyniki: {self.app.root / 'wyniki'}")

    def start(self):
        ids = self.selected() if self.selected_only.get() else None
        if ids == []:
            messagebox.showinfo("Start lokalnej kolejki", "Zaznacz lokalne nagrania do przetworzenia.", parent=self.app.window)
            return
        self.app.start(kind="local", job_ids=ids)

    def close(self):
        if self.filter_after:
            self.parent.after_cancel(self.filter_after)
            self.filter_after = None
