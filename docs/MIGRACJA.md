# Migracja repozytorium

06.10.2026: repozytorium `aspnetcore-xml-crud` otrzymuje nazwę `kolejka-transkrypcji` i kod aplikacji Kolejka transkrypcji. Historia Git i licencja MIT pozostają zachowane. Migracja dawnego ćwiczenia ASP.NET Core XML CRUD do `Evaluation-tasks/Evaluation-task1`, obok ćwiczeń 2 i 3, jest przygotowana lokalnie do publikacji 07.10.2026. Wcześniejsza publikacja migracji została cofnięta zgodnie ze zmianą kolejności wybraną przez właściciela; główna gałąź `Evaluation-tasks` wróciła do stanu sprzed migracji.

Poprzedni główny README: [README-aspnetcore-xml-crud-2026-10-06.md](archive/README-aspnetcore-xml-crud-2026-10-06.md). Odnośniki względne w archiwum opisują dawny układ projektu; kompletny kod i dokumentacja CRUD są zachowane w [ostatnim commicie przed zmianą aplikacji](https://github.com/haribo841/kolejka-transkrypcji/tree/0b078d310db7922928e4ffe67f9c6121dd16570b).

Przeniesiono wyłącznie następujące rodzaje plików ze źródła aplikacji:

- `batch/*.py`, `kolejka.py`, `transcribe.py`.
- `test_batch.py`, `test_setup.py`, `test_transcribe.py`, `test_transcript_edit.py`, `test_youtube_access.py`.
- `validate_batch_runtime.py`.
- `requirements.txt`, `requirements.lock.txt`, `requirements-batch.lock.txt`, `requirements-diarization.lock.txt`, `requirements-uvr.lock.txt`.
- `Instaluj-zaleznosci.ps1`, `Instaluj-UVR.ps1`, `Uruchom-kolejke.vbs`, `KOLEJKA.md`.

Dodano dokumentację publicznej instalacji, wykluczenia danych prywatnych, workflow Python, testy przenośności i wspólny skrypt wyboru interpreterów. Usunięto zależności od układu folderów Applio oraz osobistego katalogu XLSX. Pobranie modelu mówców kończy się prośbą o wybór własnej próbki, bez zakładania obecności lokalnego nagrania autora.

Nie kopiowano środowisk, wag, audio/wideo, wyników, raportów wcześniejszych testów, plików cookies, tokenów, baz SQLite ani osobistego arkusza YouTube. Nie uruchamiano produkcyjnej kolejki. Istniejący program i jego dane pozostają w dotychczasowym miejscu.
