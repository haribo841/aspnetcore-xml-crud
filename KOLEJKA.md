# Kolejka transkrypcji

Po [instalacji zależności i pobraniu Whispera](README.md#instalacja-i-pierwsza-próba) otwórz **Uruchom-kolejke.vbs**. Dane, modele mówców/UVR, logi i wyniki są domyślnie w `%USERPROFILE%\Transkrypcje`. Dalej używamy nazwy `FOLDER_DANYCH` dla folderu wybranego przez `--root` lub `KOLEJKA_ROOT`. `transcribe.py` obsługuje też pojedyncze nagrania.

## Pierwsze uruchomienie

1. Wybierz **Importuj katalog YouTube** w karcie YouTube albo przejdź do **Kolejka lokalna**, aby dodać folder lub pliki. Import XLSX szuka arkusza `Materiały` i nagłówków `Tytuł`, `Link`, `ID filmu`; dodatkowo rozpoznaje `Data`, `Lp.`, `Typ`, `Status`, `Widoczność`, `Długość` i `Znaczenie daty`. Wiersze bez poprawnego ID/linku pozostają jako szkice. Hiperłącze w komórce Link może prowadzić do filmu, nawet gdy jej tekst jest opisowy. Ponowny import nie tworzy duplikatów ani nie resetuje gotowych nagrań. Kolejka czeka na ręczny Start.
2. Otwórz kartę **Konfiguracja** i kliknij **Konfiguracja Community-1**, jeśli chcesz rozróżniać mówców. Kreator prowadzi przez trzy zakładki:
   - **Dostęp do modelu**: przycisk otwiera dokładnie [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1). To model autora `pyannote`, więc nie szukaj go wśród własnych modeli. Zaloguj się i wypełnij formularz dostępu na stronie modelu. Warunki obejmują udostępnienie danych kontaktowych jego autorom.
   - **Token i sprawdzenie**: na tym samym koncie otwórz **Access Tokens**, wybierz **Create new token** i w sekcji **Token type** zaznacz **Read (zalecany)**. Read wystarcza do pobrania modelu. Write dodaje niepotrzebne tu uprawnienia zapisu, a Fine-grained jest opcjonalnym wariantem z ręcznym doborem uprawnień. Nadaj nazwę, np. `Kolejka transkrypcji`, utwórz token i skopiuj jego wartość zaczynającą się od `hf_`. W kreatorze kliknij **Wklej**, a następnie **Sprawdź dostęp**. Komunikat rozróżnia niepoprawny token, brak zgody/uprawnień i problemy z połączeniem.
   - **Pobranie i próba**: po udanym sprawdzeniu kliknij **Pobierz model**. Przycisk krótkiej próby pozwala wybrać własne nagranie dowolnej długości; program przetworzy do 60 sekund. Kreator pokazuje etap i pozwala otworzyć wyniki próby. Jeśli model jest już pobrany, próba nie wymaga tokenu.

   Token nie jest zapisywany w ustawieniach, bazie ani poleceniu procesu. Jest przekazywany przez standardowe wejście procesu pobierającego. Pole tokenu jest czyszczone po rozpoczęciu pobierania lub zamknięciu kreatora.
3. W karcie **Konfiguracja** ustaw mówców, UVR i zachowywanie audio. **Pobierz / sprawdź model UVR** przygotowuje model bez tokenu. UVR zachowuje zarówno źródłowe audio, jak i wokal.
4. Kliknij **Sprawdź na fragmencie**. Program użyje zaznaczonego lokalnego nagrania lub otworzy wybór pliku, a potem przetworzy do 60 sekund z aktualnymi opcjami. Wyniki znajdziesz w `FOLDER_DANYCH\proba`. Pełna kolejka pozostaje zatrzymana. Przed Start wymagana jest zaliczona próba aktualnych ustawień; po zmianie UVR, mówców, modelu lub środowiska wykonaj ją ponownie. Sam komunikat o pobraniu modelu nie potwierdza działania.
5. Wybierz zakres i naciśnij **Start/Wznów** dla YouTube lub **Start/Wznów lokalne**. Do krótkiego pierwszego przebiegu zaznacz kilka pozycji oraz **Start tylko zaznaczonych**. Ta opcja ogranicza bieżącą sesję bez wyłączania pozostałych materiałów w bazie. Aby później przetwarzać cały zakres, wyłącz ograniczenie do zaznaczonych. Gotowe wyniki pozostają pominięte.

## Kolejka lokalna

1. Otwórz kartę **Kolejka lokalna**, ewentualnie przez skrót **Uruchom-kolejke-lokalna.vbs**. Wybierz folder lub wpisz jego ścieżkę. Sam wybór zapamiętuje folder, ale nie dodaje nagrań ani nie uruchamia transkrypcji.
2. Ustaw **Podfoldery** oraz opcjonalnie **Tylko M4A**. Wybierz **Wyniki nowych nagrań**: domyślnie **Obok źródła**, alternatywnie folder centralny lub wskazany folder. Kliknij **Dodaj folder**. Przycisk **Dodaj pliki** pozwala wybrać pojedyncze nagrania audio lub wideo.
3. Import porównuje zawartość SHA-256. Identyczne kopie pod różnymi nazwami oraz już zaimportowane nagrania są pomijane. Lista pokazuje wyłącznie lokalne zadania; wyszukiwanie obejmuje tytuł i ścieżkę źródła. Kolejność pozostaje chronologiczna, według czasu modyfikacji źródła.
4. Ustaw język lub ścieżkę audio zaznaczonych nagrań, jeśli trzeba. UVR, modele i zachowywanie audio mają wspólne ustawienia dla obu kolejek. Zmień je przed Start; oryginalne pliki i istniejące TXT w folderze źródłowym pozostają bez zmian.
5. Naciśnij **Start/Wznów lokalne**. Nie zostanie uruchomiony żaden film YouTube. Opcja **Start tylko zaznaczonych** ogranicza sesję, a nie trwałe włączenie nagrań w bazie.
6. **Dokończ bieżący i zatrzymaj** kończy całe nagranie z eksportem. Następny plik pozostaje do kolejnego Start. Zamknięcie i ponowne otwarcie okna pokazuje stan tego samego wykonawcy; punkty wznowienia są wspólne z dotychczasowym pipeline.

Jedna baza obsługuje jednego wykonawcę. Podczas sesji YouTube lokalny Start jest zablokowany, a podczas sesji lokalnej zablokowany jest Start YouTube. Aby zmienić rodzaj kolejki, zatrzymaj ją po bieżącym nagraniu. **Ponów nieudane lokalne** i lokalny raport CSV obejmują wyłącznie tę kartę. **Obróbka transkrypcji** korzysta z zaznaczenia w ostatnio używanej karcie kolejki.

Miejsce zapisu jest trwałym ustawieniem każdego zadania. **Zmień miejsce wyników** stosuje wybrany sposób zapisu do zaznaczonych nagrań, które nie rozpoczęły przetwarzania. Gotowe i częściowo przetworzone materiały zachowują swoje foldery. Szczegóły listy pokazują osobno źródło i faktyczne wyniki. Własne katalogi wyników, wokale i dane robocze są pomijane podczas skanowania.

Import w tle odczytuje długość i ścieżki audio przez FFprobe. Wspólny FFmpeg obsługuje WAV, MP3, M4A/AAC, FLAC, OGG/Opus, MP4, MKV, WebM, MOV, AVI i pozostałe obsługiwane kodeki. Uszkodzony plik, brak dźwięku albo błędna ścieżka audio otrzymuje osobny błąd i nie zatrzymuje poprawnych nagrań. Oryginały nie są usuwane ani modyfikowane.

## Konfiguracja i potwierdzenie działania

Karta **Konfiguracja** pokazuje statusy Whispera, mówców, UVR, narzędzi i YouTube. **Sprawdzanie** oznacza lekką kontrolę w tle, bez uruchamiania modeli. **Wymaga działania** zawiera przyczynę i odpowiednie ustawienia. **Gotowy do próby** potwierdza obecność plików i środowisk. Zielone **Próba zaliczona** wymaga rzeczywistej próby bieżącej konfiguracji. **Wyłączony** oznacza pomijany etap.

Raport `pierwsza-proba.json` zapisuje datę, wersje, rewizje modeli, urządzenia i opcje. Cyfrowa cisza, przy której Whisper nie został wywołany, nie potwierdza działania modelu. **Otwórz wyniki próby** pozwala ocenić tekst. Dostęp do YouTube sprawdza się osobno w **Dostęp YouTube**; problem z nim nie blokuje kolejki lokalnej. Błąd Start jest zachowany w `ostatni-blad-start.json`, a błędy procesów w logach i statusie okna.

## Zwykła praca

- Podpowiedź pojawia się po 800 ms nieruchomego kursora. Ruch, kliknięcie, przewinięcie, opuszczenie kontrolki lub zmiana okna ją zamyka; po sześciu sekundach znika sama. Tab nie otwiera popupu; **F1** wyświetla pomoc wybranej kontrolki. Dłuższy opis jest dostępny przez **Pokaż podpowiedź**, a **Pomoc** otwiera instrukcję przycisków.
- Zwykły launcher otwiera aktywną kolejkę albo ostatnio używaną kartę kolejki. Lokalny skrót zawsze otwiera lokalną kartę. Lista ustawia bieżące lub pierwsze włączone oczekujące nagranie u góry. **Bieżący / następny** wraca do tego miejsca, a zwykłe odświeżanie zachowuje Twoje zaznaczenie i przewinięcie.
- **Start/Wznów** przetwarza włączone, oczekujące nagrania YouTube od najstarszych. **Start/Wznów lokalne** obejmuje tylko lokalne pliki. Wyszukiwanie i filtry zmieniają widok; samo filtrowanie nie zmienia zakresu sesji.
- **Dokończ bieżący i zatrzymaj** kończy cały film: pobranie, opcjonalny UVR, Whisper, mówców oraz eksport. Następny film nie zostanie pobrany.
- Okno można zamknąć. Pracę wykonuje osobny proces; ponowne otwarcie pokaże jego stan. Podczas przetwarzania automatyczne uśpienie jest wstrzymane, ale ekran może się wyłączyć lub zostać zablokowany. Ręczne uśpienie, wyłączenie zasilania i restart systemu nie są blokowane.
- Po restarcie komputera sam otwórz aplikację i naciśnij Start. Zapisana kolejka nie uruchamia się automatycznie po zalogowaniu do Windows.
- **Ponów nieudane** przywraca zaznaczone problematyczne pozycje do oczekujących, a przy braku zaznaczenia wszystkie takie pozycje z danej karty. Nie rozpoczyna pracy.
- Po globalnej blokadzie YouTube Start ponawia zablokowaną pozycję. Brak modelu lub miejsca na dysku wymaga usunięcia przyczyny. Materiały prywatne, wymagające logowania, trwające transmisje i zaplanowane premiery mają osobne statusy.
- **Dodaj folder** dodaje nagrania w karcie lokalnej i opcjonalnie uwzględnia podfoldery. Pliki lokalne identyfikowane są po SHA-256. Oryginały pozostają na miejscu. Przy zmianie zawartości pliku dodaj go ponownie.
- **Wybierz ścieżkę audio** pokazuje dostępne ścieżki wskazanego pliku lokalnego. Domyślna to pierwsza. **Ustaw język** przyjmuje `auto`, `pl`, `en` i inne kody obsługiwane przez Whisper. Wybór dotyczy zaznaczonych, jeszcze nieukończonych nagrań.
- **Raport CSV** eksportuje stan do pliku otwieranego w Excelu. Oryginalny XLSX służy wyłącznie do odczytu i może pozostać otwarty w Excelu.

## Blokada YouTube i logowanie

Przycisk **Dostęp YouTube** pomaga przy komunikacie „Sign in to confirm you're not a bot”. Samo zalogowanie do przeglądarki nie zmienia anonimowego pobierania. Trzeba jeszcze świadomie wybrać tę sesję dla programu.

1. W kroku **Potwierdź w przeglądarce** otwórz zablokowany film. Zaloguj się samodzielnie do YouTube i wykonaj ewentualne potwierdzenie. Sprawdź odtwarzanie. Przycisk otwiera przeglądarkę domyślną; **Kopiuj link** pozwala użyć innej.
2. W kroku **Wybierz sesję** zaznacz **Sesja z przeglądarki** i wybierz tę samą przeglądarkę. Najprostszy odczyt w Windows zwykle daje Firefox. Przy wielu profilach wskaż folder właściwego profilu, np. odczytany na stronie `about:profiles` w Firefoksie, `chrome://version` albo `edge://version`. Puste pole pozostawia wybór profilu yt-dlp.
3. W kroku **Sprawdź dostęp** kliknij **Zapisz i sprawdź link**. Dopiero ten przycisk odczytuje wybraną sesję i wykonuje pojedyncze sprawdzenie formatów filmu. Nie pobiera nagrania do transkrypcji, nie ponawia testu automatycznie i nie zmienia statusów kolejki. Zamknięcie okna anuluje test.
4. Jeśli formaty są dostępne, zamknij okno i naciśnij **Start/Wznów**. Wznawiany jest film z blokadą; gotowe wyniki pozostają pominięte. Dostępność formatów nie gwarantuje powodzenia późniejszego pobrania strumienia. Inne wcześniejsze błędy można zaznaczyć i przywrócić przez **Ponów nieudane**.

Chrome i Edge mogą blokować kopiowanie bazy cookies albo jej odszyfrowanie w Windows. Najpierw zamknij przeglądarkę całkowicie po zalogowaniu i ponów pojedynczy test. Jeśli problem pozostaje, użyj Firefoksa albo opcji **Plik cookies.txt wyeksportowany dla YouTube**. Program nie wyłącza szyfrowania ani innych zabezpieczeń przeglądarki i nie przechodzi po błędzie po cichu na pobieranie anonimowe.

Wariant plikowy przyjmuje format **Netscape cookies.txt**, nie JSON i nie token Hugging Face. Zgodnie z [instrukcją yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies) można otworzyć osobne okno prywatne, zalogować się do YouTube, przejść w tej samej karcie na `https://www.youtube.com/robots.txt`, wyeksportować cookies wyłącznie `youtube.com` odpowiednim rozszerzeniem do formatu Netscape, a następnie zamknąć to okno. Dokumentacja [FAQ yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp) wskazuje rozszerzenia do eksportu. Tryb **Sesja z przeglądarki** nie odczytuje okien prywatnych; w tym wariancie wskaż wyeksportowany plik.

Cookies dają dostęp do sesji konta. Nie wysyłaj ich na czat ani innym osobom. Program wykorzystuje tylko aktualne cookies domeny YouTube, nie nadpisuje pliku źródłowego i nie zapisuje ich wartości w raportach, transkrypcjach ani logach. Odczyt sesji z przeglądarki korzysta z bazy wybranego profilu. Osobny plik `FOLDER_DANYCH\youtube-dostep.json` przechowuje wyłącznie sposób dostępu, ścieżki i odstępy. **Zapisz ustawienia** nie odczytuje cookies; wybrana sesja zostanie użyta przy następnym teście lub Start. Wybór **Anonimowo** wyłącza korzystanie z niej.

Domyślne przerwy wynoszą 15 sekund przed próbą pobrania filmu i 1 sekundę pomiędzy żądaniami metadanych yt-dlp. Przerwę przed filmem można zmienić w oknie na 5-300 sekund. Pobieranie nadal odbywa się pojedynczo. Ukończone pobrania są wykorzystywane ponownie bez odczytu cookies i bez dodatkowej zwłoki. Zmiana sesji nie unieważnia bloków transkrypcji ani diarizacji.

Logowanie i przerwy nie gwarantują usunięcia limitu konta lub adresu IP. Gdy YouTube nadal odmawia dostępu, pozostaw kolejkę zatrzymaną i spróbuj później. Duże pobrania mogą spowodować ograniczenie także zalogowanego konta, co opisuje [dokumentacja yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies). Błąd antybotowy lub limit żądań zatrzymuje kolejkę po pierwszej odmowie; nie powoduje serii prób na kolejnych filmach.

## UVR i zachowanie audio

Przepływ po włączeniu opcji:

```text
YouTube / plik lokalny
    -> zapis źródłowego audio
    -> UVR: wydzielenie wokalu w blokach 5 minut
    -> wokal.flac
    -> mono 16 kHz
    -> Whisper + rozróżnianie mówców
    -> TXT / SRT / VTT / JSON
```

Integracja używa modeli Ultimate Vocal Remover przez lokalną bibliotekę `audio-separator`, bez automatyzowania kliknięć w GUI UVR. Domyślny model to `UVR-MDX-NET-Voc_FT.onnx`. Separacja korzysta z osobnego środowiska `.venv-uvr`. CUDA jest sprawdzana w praktyce, a nieudane obliczenie GPU jest ponawiane na CPU z komunikatem w oknie i logu.

Każdy ukończony blok UVR ma sumę kontrolną i punkt wznowienia. Bloki mają po 3 sekundy kontekstu z każdej strony. Wokal jest składany według absolutnych numerów próbek bez wycinania ciszy i zmiany tempa. To zachowuje oś czasu potrzebną napisom. Na granicach separacji mogą wystąpić drobne artefakty; posłuchaj wyniku krótkiej próby przed dużą sesją.

Włączenie UVR automatycznie włącza zachowanie źródła. Można też zaznaczyć samo **Zachowuj źródłowe audio**, bez separacji. Ustawienia zmieniaj po zatrzymaniu kolejki. Dotyczą następnej sesji i nie przetwarzają ponownie materiałów już oznaczonych jako Gotowe.

W folderze filmu powstaje `audio\zrodlo.<rozszerzenie>` oraz, przy UVR, `audio\wokal.flac` (stereo 44,1 kHz, FLAC 24 bit). Jeżeli źródło zawiera obraz, zachowywana jest jego wybrana ścieżka audio w MKA bez ponownego kodowania. Przy źródle zawierającym wyłącznie audio zachowywana jest dokładna kopia pliku. Tymczasowy PCM i pomocnicze bloki UVR są usuwane po zweryfikowaniu wyników. Trwałe pliki audio nie są usuwane.

UVR oddziela głos od części tła, ale nie rozdziela automatycznie wszystkich osób na osobne pliki. Modele wokalu mogą zachować śpiew i usunąć fragmenty cichej mowy. Poprawę jakości transkrypcji należy ocenić na własnym materiale. Do porównania pozostaje źródłowe audio.

## Wyniki i wznowienie

Każde nagranie ma osobny folder `tytuł__stabilny-identyfikator` w swoim zapisanym miejscu wyników. Dla YouTube i starszych zadań pozostaje to `FOLDER_DANYCH\wyniki`; dla nowych lokalnych nagrań domyślnie podfolder obok źródła. Zawiera:

- `tytuł__yt-ID_transkrypcja.txt`: czasy i etykiety Mówca 1, Mówca 2 itd.
- Pliki `.srt` i `.vtt` z tą samą nazwą bazową: napisy z etykietami mówców.
- Plik `.json` z tą samą nazwą bazową: słowa, segmenty, przedziały mówców, nakładanie się głosów, embeddingi, konfiguracja i wersje bibliotek, surowe wyniki bloków.
- `gotowe.json`: sumy kontrolne wymaganych wyników i zachowywanych ścieżek audio.
- `robocze`: punkty wznowienia i logi etapów. Przy błędzie pozostają również potrzebne pliki audio.

Nazwa pliku zawiera skrócony tytuł i ID YouTube albo fragment skrótu pliku lokalnego. Materiały o tym samym tytule dostają różne nazwy. Ponowny eksport do zajętej nazwy tworzy `__wersja-2`, `__wersja-3` itd. Wcześniejsze wersje pozostają na dysku. Program odmawia zapisania wyników innego nagrania do katalogu, który ma już właściciela.

**Otwórz TXT** otwiera transkrypcję jednego zaznaczonego, ukończonego nagrania. Wcześniejsze wyniki o nazwie `transkrypcja.txt` w osobnych katalogach nadal są obsługiwane; nowe eksporty korzystają z nazwy nagrania i identyfikatora.

Whisper działa na CPU, w blokach 10 minut z kontekstem 5 sekund. Pyannote działa w blokach 30 minut z kontekstem 10 sekund. Nie trzeba ręcznie dzielić plików. Przetwarzanie używa pliku PCM mapowanego do pamięci i tablic NumPy; nie tworzy list Python obejmujących wielogodzinne audio.

Stan Gotowe oznacza kompletne, sprawdzone pliki wynikowe, a nie ręcznie potwierdzoną poprawność każdego słowa. Automatyczne rozpoznawanie języka bywa zawodne przy krótkich, mieszanych językowo fragmentach, ciszy i muzyce. W takich materiałach warto wymusić język. JSON zachowuje także hipotezy z kontekstu na granicach bloków.

Łączenie mówców wykorzystuje odległość embeddingów i próg odczytany z rzeczywistej konfiguracji Community-1. Odrębni mówcy z tego samego bloku nie mogą zostać połączeni. Łączenie bloków jest dodatkową heurystyką, a nie gwarancją identyfikacji. Numeracja zaczyna się od nowa w każdym filmie. Nierozstrzygnięte słowa otrzymują etykietę Mówca nieustalony.

SQLite pracuje z transakcjami WAL i pełną synchronizacją. Blokada systemowa dopuszcza jednego wykonawcę. Po awarii wykorzystuje on pliki i bloki ze sprawdzonymi sumami kontrolnymi; nieukończony blok jest obliczany ponownie. Zmiana modelu, języka, ścieżki lub parametrów bloku unieważnia niepasujące punkty wznowienia. Awaria procesu głównego kończy również jego procesy dekodowania, UVR i diarizacji.

## Obróbka transkrypcji

Druga karta **Obróbka transkrypcji** działa na gotowych tekstach, bez ponownego przetwarzania audio.

1. Wybierz **Wszystkie gotowe**, **Zaznaczone z kolejki** albo **Dodaj pliki TXT / SRT / VTT**.
2. Zostaw zaznaczone **Usuń oznaczenia mówców**. Zaznacz plik na liście, aby porównać oryginał i kopię. Podgląd pokazuje pierwsze 50 000 znaków, a zapis obejmuje cały plik.
3. Wybierz **Zapisz zaznaczone** lub **Zapisz wszystkie**. Domyślnie kopie trafiają do `FOLDER_DANYCH\obrobione`; folder można zmienić.
4. **Otwórz folder kopii** pokazuje wynik. Każda kopia ma dopisek `_bez_mowcow`. Zajęta nazwa powoduje dodanie numeru; żaden wcześniejszy plik nie jest zastępowany.

Przykład:

```text
Oryginał:
[00:12:34.200 - 00:12:39.800] Mówca 2: Treść wypowiedzi.

Kopia:
[00:12:34.200 - 00:12:39.800] Treść wypowiedzi.
```

Usuwane są rozpoznane etykiety na początku wypowiedzi, np. `Mówca 2:`, `Mówca nieustalony:` i `Speaker_00:`, oraz znaczniki głosu `<v ...>` w VTT. Timestampy, treść, numeracja napisów i podziały wierszy pozostają zachowane. Nazwy mówców wspomniane wewnątrz wypowiedzi pozostają tekstem. Nieobsługiwane oznaczenia zostają bez zmian, co widać w podglądzie. Obsługiwane kodowania to UTF-8 oraz UTF-16 z BOM.

**Usuń z listy** i **Wyczyść listę** nie usuwają plików. **Zatrzymaj zapisywanie** kończy serię po bieżącym pliku. Zamknięcie aplikacji podczas zapisu kopii ukrywa okno i pozwala dokończyć rozpoczętą serię. Obróbka tekstów nie zmienia statusów ani ustawień kolejki nagrań.

## Utrzymanie

Ustawienia zaawansowane: `FOLDER_DANYCH\ustawienia.json`. Pierwsze otwarcie tworzy ten plik. Edytuj po zatrzymaniu wykonawcy. Ścieżki `ffmpeg`, `ffprobe` i `node` są wykrywane z PATH; można wpisać własne pełne ścieżki. Nie ustawiaj progów mówców arbitralnie; aplikacja odczytuje je z modelu.

Instalatory odtwarzające przypięte zależności: `Instaluj-zaleznosci.ps1` i opcjonalny `Instaluj-UVR.ps1`. Używają PyTorch CUDA 12.6; wykonanie na danym GPU i sterownikach jest sprawdzane podczas pracy, z przejściem na CPU po niepowodzeniu. Pobieranie YouTube wykorzystuje przypięte `yt-dlp` i EJS, lokalny Node.js 22 oraz FFmpeg 9. Domyślnie działa anonimowo; sesja przeglądarki lub plik cookies są używane wyłącznie po wybraniu ich w **Dostęp YouTube**. Modele są pobierane raz; lokalna transkrypcja, diarizacja i separacja nie wysyłają audio do zewnętrznych usług.

Przykłady PowerShell, wykonywane w folderze programu:

```powershell
.\.venv\Scripts\python.exe kolejka.py status
.\.venv\Scripts\python.exe kolejka.py stop
.\.venv\Scripts\python.exe kolejka.py csv "$env:USERPROFILE\Transkrypcje\postep.csv"
.\.venv\Scripts\python.exe kolejka.py gui --youtube
.\.venv\Scripts\python.exe -m unittest discover -v
```

`validate_batch_runtime.py --root NOWY_ODDZIELNY_FOLDER --sample WŁASNA_PRÓBKA --model LOKALNY_MODEL_WHISPER` wykonuje rzeczywiste próby zamknięcia okna, awarii procesu, wznowienia i trzygodzinnej ciszy. [Szczegóły walidacji](docs/WERYFIKACJA.md). Ten test infrastruktury świadomie wyłącza diarizację; pełną próbę bieżącego pipeline'u wykonuje przycisk Sprawdź na fragmencie.

Źródła techniczne: [OpenVINO WhisperPipeline](https://docs.openvino.ai/2026/api/genai_api/_autosummary/openvino_genai.WhisperPipeline.html), [Community-1](https://huggingface.co/pyannote/speaker-diarization-community-1), [yt-dlp EJS](https://github.com/yt-dlp/yt-dlp/wiki/EJS), [audio-separator](https://github.com/nomadkaraoke/python-audio-separator), [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui).
