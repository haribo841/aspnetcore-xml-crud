# Walidacja samodzielnego checkoutu

08.10.2026: przed publikacją bieżącej wersji ponownie przeszło 111 testów bez pominięć, kompilacja modułów i `pip check`, na Pythonie 3.13.1. Kod zmienionych modułów oraz skrót karty lokalnej są identyczne z obecną instalacją aplikacji. Zachowano bajtową kopię poprzedniego głównego README. Testy używały katalogów tymczasowych i danych syntetycznych; nie uruchamiano produkcyjnej kolejki. Wynik GitHub Actions dla nowego commitu sprawdzany jest osobno po push.

06.10.2026, po dodaniu karty lokalnej: lokalnie przeszło 111 testów tego checkoutu oraz 102 testy zaktualizowanej istniejącej instalacji, bez pominięć. Serie uruchomiono istniejącym interpreterem Python 3.13.1 i jego zainstalowanymi zależnościami. Nie instalowano nowych modeli ani nie uruchamiano pełnej kolejki. Nowe zmiany pozostają lokalne. [GitHub Actions dla wcześniejszego commitu f7cf580](https://github.com/haribo841/kolejka-transkrypcji/actions/runs/37438503418) przeszło, ale nie obejmuje jeszcze rozbudowy karty lokalnej.

Nowe próby obejmują migrację istniejącej bazy SQLite, niezależne zakresy YouTube/lokalne, trwały wybór ID, brak zmiany włączenia innych zadań, pomijanie Node dla plików lokalnych i ochronę przed drugim Start podczas pracy oraz uruchamiania wykonawcy. Testy GUI sprawdzają M4A, podfoldery, SHA-256, import bez Start, ponawianie błędów tylko jednej kolejki oraz zaznaczenia przekazywane do obróbki tekstu. Osobna próba z syntetycznym procesorem potwierdziła zamknięcie i ponowne otwarcie okna podczas pracy oraz zatrzymanie po bieżącym nagraniu. Karta mieści się przy 1360×860 i 1220×760 również z długimi komunikatami.

Zakres obejmuje syntetyczny XLSX z nagłówkami w zmienionej kolejności i hiperłączem, deduplikację, zachowanie źródeł, kolejkę SQLite, zatrzymanie po bieżącym filmie, wznowienie bloków, kontrolę integralności, wersjonowanie eksportu, obróbkę mówców, konfigurację dostępu Hugging Face, cookies wyłącznie YouTube i test jednego linku. Rzeczywisty FFmpeg dekodował syntetyczne audio, wybierał ścieżkę i odrzucał błędny plik. Kontrolki Tk uruchamiano w testowych oknach na katalogach tymczasowych.

Przeszły kompilacja modułów, `pip check`, sprawdzenie składni trzech skryptów PowerShell i parsowanie YAML workflow. Pięć plików zależności jest identycznych bajtowo z wersją istniejącej aplikacji. Sprawdzono wykluczenie środowisk, modeli, plików cookies, konfiguracji, XLSX, baz, audio i wyników przez `.gitignore`. Nie przeprowadzono nowej instalacji zależności ani ponownej próby modeli w samodzielnym checkoutcie. Walidator bez modelu zwrócił jawny SKIP z kodem 2; testy odrzucają ponowne użycie niepustego folderu walidacji.

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
