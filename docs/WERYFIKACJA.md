# Walidacja samodzielnego checkoutu

06.10.2026: lokalnie przeszło 79 testów, bez pominięć. Serię uruchomiono z folderu tego checkoutu, wykorzystując istniejący interpreter Python 3.13.1 i jego zainstalowane zależności. Nie instalowano nowych modeli ani nie wykonywano inference w przeniesionym checkoutcie. Workflow GitHub Actions jest przygotowany; jego zdalny wynik wymaga publikacji zmian.

Zakres obejmuje syntetyczny XLSX z nagłówkami w zmienionej kolejności i hiperłączem, deduplikację, zachowanie źródeł, kolejkę SQLite, zatrzymanie po bieżącym filmie, wznowienie bloków, kontrolę integralności, wersjonowanie eksportu, obróbkę mówców, konfigurację dostępu Hugging Face, cookies wyłącznie YouTube i test jednego linku. Rzeczywisty FFmpeg dekodował syntetyczne audio, wybierał ścieżkę i odrzucał błędny plik. Kontrolki Tk uruchamiano w testowych oknach na katalogach tymczasowych.

Przeszły kompilacja modułów, `pip check`, sprawdzenie składni trzech skryptów PowerShell i parsowanie YAML workflow. Pięć plików zależności jest identycznych bajtowo z wersją istniejącej aplikacji. Sprawdzono wykluczenie środowisk, modeli, plików cookies, konfiguracji, XLSX, baz, audio i wyników przez `.gitignore`. Nie przeprowadzono nowej instalacji zależności ani zdalnego CI aplikacji. Walidator bez modelu zwrócił jawny SKIP z kodem 2; testy odrzucają ponowne użycie niepustego folderu walidacji.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe -m compileall -q batch kolejka.py transcribe.py
.\.venv\Scripts\python.exe -m pip check
```

Testy zwykłe nie potrzebują osobistego XLSX, cookies, danych konta, GPU ani pobranych modeli. Trzy próby FFmpeg są jawnie pomijane, gdy brakuje narzędzi. Pominięcie nie jest wynikiem rzeczywistego dekodowania. CI celowo nie loguje się do usług ani nie pobiera wag.

## Próby integracyjne na własnych danych

Po pobraniu Whispera można jawnie uruchomić walidator infrastruktury Windows:

```powershell
.\.venv\Scripts\python.exe validate_batch_runtime.py --root 'D:\TestyKolejki\nowa-proba' --sample 'D:\Nagrania\krotka-mowa.wav' --model '.\models\whisper-large-v3-turbo-fp16-ov'
```

Wybierz nowy, oddzielny folder. Walidator sprawdza zamknięcie i ponowne otwarcie GUI, przerwanie procesu, wznowienie, czas życia procesu podrzędnego oraz trzygodzinną ciszę. Brak modelu lub próbki zwraca SKIP i kod 2 przed rozpoczęciem prób. Wykonanie może zająć czas i miejsce na dysku. Te próby wyłączają diarizację i nie są oceną jakości mówców ani gwarancją transkrypcji wielogodzinnej rozmowy.

Pełny test Whisper + Community-1 + opcjonalny UVR wykonuje przycisk Próba lokalna po skonfigurowaniu modeli. Jakość polskiego, angielskiego, muzyki, nakładania głosów i łączenia mówców należy sprawdzić na reprezentatywnych nagraniach. Skuteczność dostępu do YouTube wymaga osobnego rzeczywistego testu linku w aplikacji.
