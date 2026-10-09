"""Mouse and keyboard help shared by the queue and its setup wizard."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def status_tags(status):
    from .store import RETRYABLE
    if status in {"done", "draft"}:
        return (status,)
    if status in RETRYABLE:
        return ("error",)
    return ()


def sync_tree(tree, visible, rows):
    """Refresh rows while retaining the selection and avoiding repeated inserts."""
    selected = set(tree.selection())
    wanted = []
    for item, values, tags in rows:
        wanted.append(item)
        if visible.get(item) != values:
            if tree.exists(item):
                tree.item(item, values=values, tags=tags)
            else:
                tree.insert("", "end", iid=item, values=values, tags=tags)
            visible[item] = values
    wanted_set = set(wanted)
    for item in list(visible):
        if item not in wanted_set:
            tree.delete(item)
            del visible[item]
    if tree.get_children() != tuple(wanted):
        for index, item in enumerate(wanted):
            tree.move(item, "", index)
    tree.selection_set(*sorted(selected & wanted_set))


class Tooltip:
    def __init__(self, widget, text, status=None, delay=450):
        self.widget, self.text, self.status, self.delay = widget, text, status, delay
        self.pending, self.popup = None, None
        widget.help_text = text
        widget.tooltip = self
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<FocusIn>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<FocusOut>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Escape>", self.hide, add="+")
        widget.bind("<F1>", self.show, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def schedule(self, event=None):
        self.hide()
        if self.status is not None:
            self.status.set(self.text)
        self.pending = self.widget.after(self.delay, self.show)

    def show(self, event=None):
        self.cancel()
        if self.popup:
            return "break"
        if not self.widget.winfo_exists() or not self.widget.winfo_viewable():
            return None
        popup = self.popup = tk.Toplevel(self.widget)
        popup.withdraw()
        popup.wm_overrideredirect(True)
        popup.attributes("-topmost", True)
        frame = tk.Frame(popup, bg="#fffbe6", borderwidth=1, relief="solid")
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=self.text, justify="left", wraplength=420,
                 background="#fffbe6", foreground="#202020", padx=12, pady=9).pack()
        popup.update_idletasks()
        x = min(self.widget.winfo_rootx() + 10,
                self.widget.winfo_screenwidth() - popup.winfo_reqwidth() - 12)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        if y + popup.winfo_reqheight() > self.widget.winfo_screenheight() - 12:
            y = self.widget.winfo_rooty() - popup.winfo_reqheight() - 6
        popup.geometry(f"+{max(0, x)}+{max(0, y)}")
        popup.deiconify()
        return "break"

    def cancel(self):
        if self.pending:
            try:
                self.widget.after_cancel(self.pending)
            except tk.TclError:
                pass
            self.pending = None

    def hide(self, event=None):
        self.cancel()
        if self.popup:
            try:
                self.popup.destroy()
            except tk.TclError:
                pass
            self.popup = None


def help_button(parent, text, command, help_text, status=None, **kwargs):
    button = ttk.Button(parent, text=text, command=command, **kwargs)
    Tooltip(button, help_text, status)
    return button


def wrap_with_parent(label, padding):
    label.master.bind("<Configure>", lambda event: label.configure(
        wraplength=max(240, event.width - padding)), add="+")


HELP = {
    "import": "Otwiera katalog Excel z arkuszem Materiały i dodaje linki YouTube. Powtórny import pomija duplikaty. Nie rozpoczyna pobierania.",
    "files": "Dodaje wybrane pliki audio lub wideo z dysku. Oryginały pozostają na miejscu. Po imporcie naciśnij Start.",
    "folder": "Dodaje nagrania z folderu i wszystkich jego podfolderów. Porównuje zawartość plików, aby nie dublować pracy.",
    "speakers": "Otwiera przewodnik: bezpośrednia strona Community-1, token Hugging Face, sprawdzenie dostępu i pobranie. Konfiguracja jest jednorazowa.",
    "test": "Przetwarza wybrane nagranie do 2 minut i sprawdza zapis wyników. Używa aktualnych opcji UVR i mówców. Nie uruchamia katalogu YouTube.",
    "youtube_access": "Pomaga przy blokadzie YouTube: otwiera film do ręcznego potwierdzenia, pozwala wybrać sesję przeglądarki lub cookies.txt i przetestować jeden link. Nie wznawia kolejki automatycznie.",
    "csv": "Zapisuje status wszystkich nagrań w CSV do otwarcia w Excelu. Nie zmienia źródłowego katalogu XLSX.",
    "guide": "Otwiera krótką instrukcję przycisków i kolejności pierwszego uruchomienia.",
    "uvr": "Przed transkrypcją oddziela głos od muzyki modelem UVR. Zachowuje źródłowe audio i wokal.flac. Może pomóc przy tle muzycznym, ale wydłuża obliczenia.",
    "keep": "Zachowuje kopię źródłowej ścieżki audio w folderze wyników. UVR włącza tę opcję automatycznie. Oryginalne pliki lokalne nigdy nie są usuwane.",
    "uvr_model": "Jednorazowo pobiera model oddzielający wokal od tła. Nie wymaga konta ani tokenu Hugging Face. Już pobrany model zostanie wykorzystany ponownie.",
    "search": "Filtruje listę według tytułu lub identyfikatora filmu. Zmienia tylko widok, a nie to, które nagrania są włączone do kolejki.",
    "date_from": "Pokazuje materiały od tej daty włącznie. Wpisz np. 2020-01-01. Puste pole oznacza brak ograniczenia.",
    "date_to": "Pokazuje materiały do tej daty włącznie. Wpisz datę jako RRRR-MM-DD. Puste pole oznacza brak ograniczenia.",
    "status": "Pokazuje tylko nagrania o wybranym statusie, np. Oczekuje albo Błąd. Filtr nie wyłącza pozostałych z kolejki.",
    "type": "Ogranicza widok do YouTube, plików lokalnych, filmów, transmisji lub Shorts. Nie zmienia kolejności przetwarzania.",
    "list": "Kliknij wiersz, aby zobaczyć jego opis lub błąd. Ctrl/Shift zaznacza wiele pozycji; Ctrl+A zaznacza widoczne. Dwuklik otwiera folder wyników. Kolumna Kolejka określa, czy Start obejmie pozycję.",
    "select": "Zaznacza wszystkie pozycje widoczne po zastosowaniu filtrów. Samo zaznaczenie nie dodaje ich do przetwarzania ani go nie rozpoczyna.",
    "enable": "Włącza zaznaczone, oczekujące materiały do kolejnej sesji Start. Nie resetuje gotowych nagrań.",
    "disable": "Pomija zaznaczone materiały przy następnym Start. Pozostają w bazie i można je później ponownie włączyć.",
    "language": "Ustawia język dla zaznaczonych, nieukończonych nagrań: auto wykrywa język; pl lub en wymusza polski albo angielski. Nie tłumaczy wypowiedzi.",
    "track": "Dla jednego zaznaczonego pliku lokalnego pokazuje dostępne ścieżki audio i pozwala wybrać jedną. Domyślnie używana jest pierwsza.",
    "progress": "Postęp bieżącego etapu jednego filmu. Po przejściu do kolejnego etapu procent zaczyna się od nowa. Ukończone bloki pozostają zapisane.",
    "start": "Rozpoczyna lub wznawia włączone nagrania YouTube od najstarszych. Pliki lokalne uruchamia się w karcie Kolejka lokalna. Gotowe materiały są pomijane. Sam filtr listy nie ogranicza zakresu Start.",
    "stop": "Zapisuje żądanie zatrzymania po całym bieżącym filmie: pobraniu, UVR, transkrypcji, mówcach i eksporcie. Nie przerywa aktualnego etapu. Następny film nie ruszy.",
    "retry": "Przywraca problematyczne nagrania do oczekujących. Działa na zaznaczonych; bez zaznaczenia obejmuje wszystkie nieudane. Potem naciśnij Start.",
    "results": "Otwiera folder wyników jednego zaznaczonego filmu. Przy wielu zaznaczeniach lub braku zaznaczenia otwiera wspólny folder wyników.",
    "transcript": "Otwiera plik TXT jednego zaznaczonego, ukończonego nagrania. Nazwa pliku zawiera tytuł i identyfikator nagrania.",
    "selected_only": "Ogranicza tę sesję do zaznaczonych nagrań YouTube. Nie wyłącza innych materiałów w bazie. Przy kolejnym Start możesz wrócić do całego katalogu.",
}
