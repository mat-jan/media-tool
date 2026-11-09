# media-tool

Narzędzie CLI do **identyfikacji, diagnostyki, raportowania, kopii zapasowych i obrazowania nośników danych**:
dysków, pendrive'ów, kart SD, czytników kart. Działa na **macOS, Linux i Windows**, w czystym Pythonie
(3.9+, bez zależności).

**Zasada nadrzędna:** użytkownik sam wybiera, co chce zrobić. Wszystko jest **tylko do odczytu**, dopóki wyraźnie
nie wybierzesz innej operacji. **Jedyna operacja zapisu na nośnik to przywracanie obrazu** (`image restore`) –
dozwolona tylko na nośnikach zewnętrznych, z podwójnym potwierdzeniem i obowiązkową weryfikacją zapisu.

## Spis treści
- [Instalacja – bez instalowania pakietów](#instalacja--bez-instalowania-pakietów)
- [Bezpieczeństwo](#bezpieczeństwo)
- [Polecenia](#polecenia)
- [Menu interaktywne](#menu-interaktywne)
- [Co program pokazuje](#co-program-pokazuje)
- [Backup plików](#backup-plików)
- [Obrazy nośników](#obrazy-nośników)
- [Dostępność funkcji na systemach](#dostępność-funkcji-na-systemach)
- [Stan testów i znane ograniczenia](#stan-testów-i-znane-ograniczenia)
- [Rozwój](#rozwój)

## Instalacja – bez instalowania pakietów

> **English:** `media-tool` identifies, diagnoses, reports on and images storage media (SD cards, USB drives, SSD/HDD)
> on macOS, Linux and Windows. It is **read-only by design**, needs no dependencies, and is signed by nobody – so use
> the installers below, which avoid the macOS/Windows "unverified developer" warnings.
> macOS / Linux: `curl -fsSL https://raw.githubusercontent.com/mat-jan/media-tool/main/install.sh | sh`
> Windows (PowerShell): `irm https://raw.githubusercontent.com/mat-jan/media-tool/main/install.ps1 | iex`

### Najłatwiej: jedna komenda (bez ostrzeżeń systemu)

**macOS i Linux** – wklej w Terminalu:

```bash
curl -fsSL https://raw.githubusercontent.com/mat-jan/media-tool/main/install.sh | sh
```

**Windows** – wklej w PowerShellu:

```powershell
irm https://raw.githubusercontent.com/mat-jan/media-tool/main/install.ps1 | iex
```

Instalator pobiera najnowsze wydanie (także beta), sprawdza sumę SHA-256 (jeśli wydanie ją zawiera), rozpakowuje do
katalogu użytkownika (nie wymaga administratora) i nie nadpisuje katalogów, których sam nie utworzył.
Potem uruchamiasz `media-tool` (menu) – albo `sudo media-tool` do obrazów, skanu i testów (macOS/Linux);
na Windows: skrót „media-tool" na pulpicie. Odinstalowanie: ta sama komenda z `-s -- --uninstall`
(Windows: `$env:MEDIA_TOOL_UNINSTALL=1` przed komendą). Pliki pobrane przez `curl` i PowerShell nie dostają znacznika
„z internetu", więc macOS Gatekeeper i Windows SmartScreen nie wyświetlają ostrzeżenia.

### Albo: pobierz archiwum i kliknij dwukrotnie

Pobierz paczkę ze strony **Releases**, rozpakuj i kliknij skrypt dla swojego systemu. Skrypt zapyta, czy pracować
w trybie podstawowym (tylko informacje), czy pełnym (obrazy, skan, testy – poprosi o hasło/zgodę administratora).

| System | Plik do kliknięcia | Co jest potrzebne |
|---|---|---|
| Windows | `Start-Windows.bat` | nic, jeśli pobrałeś paczkę `windows-x64` (zawiera `media-tool.exe`); w paczce `universal`: Python 3.9+ |
| macOS | `Start-macOS.command` | nic, jeśli pobrałeś paczkę `macos-arm64` / `macos-x64`; w paczce `universal`: Python 3.9+ |
| Linux | `Start-Linux.sh` (menedżer plików: „Uruchom w terminalu") | nic dodatkowego w paczce `linux-x64`; `lsblk` z `util-linux` (jest wszędzie) |

Paczki: `media-tool-<system>.zip` = samodzielny program (bez Pythona) + skrypt startowy;
`media-tool-universal.zip` = jeden plik `media-tool.pyz` (62 KiB) + skrypty dla wszystkich systemów (wymaga Pythona 3.9+).
Skrypt startowy sam wybiera, co uruchomić: gotowy program → `media-tool.pyz` → katalog źródłowy `media_tool/`.
Raporty i obrazy trafiają domyślnie do `~/media-tool` (`%USERPROFILE%\media-tool`; zmiana: `MEDIA_TOOL_OUTPUT_DIR`).

**Ostrzeżenia przy pobraniu z przeglądarki** (program nie jest podpisany certyfikatem – to płatna usługa Apple/Microsoft):
* **macOS „Apple could not verify…":** kliknij **Done** (nie „Move to Trash"). Potem albo
  *Ustawienia systemowe → Prywatność i ochrona →* przewiń w dół → **„Otwórz mimo to"**, albo w Terminalu:
  `xattr -dr com.apple.quarantine <rozpakowany-katalog>` i kliknij plik ponownie.
  (Starszy sposób „prawy klik → Otwórz" nie działa od macOS 15.) Jeśli bit wykonywalny zginął: `bash Start-macOS.command`.
* **Windows SmartScreen:** „Więcej informacji" → „Uruchom mimo to".

### Dla użytkowników Pythona

```bash
pipx install git+https://github.com/mat-jan/media-tool.git   # polecenie `media-tool`
python3 media-tool.pyz                                       # z paczki universal, bez instalacji
python3 -m media_tool list                                   # z kodu źródłowego, bez instalacji
```

Odczyt **surowych urządzeń** (obrazowanie, skan, test integralności, test prędkości) wymaga uprawnień
administratora (`sudo …` / terminal „jako administrator"). Informacje o urządzeniach (`list`, `inspect`, `serial`, …)
działają bez nich. Opcjonalnie: `smartctl` (pakiet *smartmontools*) – pełne dane SMART.

Paczki buduje GitHub Actions (`.github/workflows/build.yml`): testy na Ubuntu/macOS/Windows i Pythonie 3.9 oraz 3.13,
potem PyInstaller na każdym systemie; przy tagu `v*` powstaje Release z sumami `SHA256SUMS.txt`.


## Bezpieczeństwo

| Poziom | Operacje | Wymagane potwierdzenie |
|---|---|---|
| Metadane systemu | `list`, `inspect`, `serial`, `ids`, `uuids`, `partitions`, `mount-status`, `system-status`, `space`, `health` | brak (to tylko odczyt informacji od systemu) |
| Odczyt nośnika | `benchmark`, `integrity`, `scan`, `errors`, `image create/resume/verify` | pokazanie danych urządzenia + `t/n` (`--yes` pomija pytanie) |
| Kopia plików | `backup run` | pokazanie danych urządzenia + `t/n`; czyta pliki z zamontowanego woluminu, zapisuje **tylko** w folderze docelowym |
| Zmiana stanu | `unmount`, `eject` | wpisanie **pełnej ścieżki**, **numeru seryjnego** (jeśli jest) i słowa **`POTWIERDZAM`**; `--yes` **nie działa** |
| **Zapis na nośnik** | `image restore` | jak wyżej, ale słowo **`NADPISZ`** + ostrzeżenie o nieodwracalności; tylko nośniki zewnętrzne, nie systemowe, nie tylko-do-odczytu; obraz sprawdzany przed zapisem, zapis weryfikowany po; **niedostępne na Windows** |

* Urządzenia nigdy nie wybiera się „po cichu" tylko po nazwie: przed każdą operacją na nośniku widzisz producenta,
  model, numer seryjny, pojemność, ścieżkę, typ, status montowania, czy jest systemowy, partycje i punkty montowania.
  Można też wybrać po cechach: `serial:XYZ`, `wwn:…`, `uuid:…`. Niejednoznaczny wybór jest odrzucany.
* Po potwierdzeniu dane urządzenia są **odczytywane ponownie**; jeśli się zmieniły (inny numer seryjny, rozmiar,
  partycje, zamontowanie…) operacja jest przerywana.
* **Dysk systemowy** – odmontowanie, odłączenie i przywracanie zablokowane bezwarunkowo; operacje odczytu tylko
  z ostrzeżeniem. Przywracanie odmawia też, gdy **nie da się ustalić**, czy dysk jest systemowy.
* Poza `image restore` nośnik jest otwierany wyłącznie do odczytu. Plik obrazu **nie może leżeć na obrazowanym nośniku** (sprawdzane
  po identyfikatorze woluminu i ścieżce) i nie nadpisuje istniejących plików bez `--overwrite`.
* `--dry-run` / `dry-run POLECENIE` – pokazuje plan i sprawdza warunki wstępne, niczego nie czyta ani nie zmienia.
* Każda operacja trafia do logu (`media-tool logs`); raporty można generować z **zamaskowanymi numerami seryjnymi**.
* Brakujące informacje to zawsze **`NIEDOSTĘPNE`** – program niczego nie zgaduje.

> **Karty SD:** numer seryjny często jest niedostępny albo należy do **czytnika/mostka USB**, a nie do karty.
> Program rozdziela pola: numer urządzenia, kontrolera, **karty SD** (rejestr CID – tylko gdy sprzęt go udostępnia,
> np. natywny slot MMC w Linuksie) i **czytnika**, i ostrzega o tym przy każdym odczycie.

## Polecenia

Flagi globalne (przed lub po poleceniu): `--json`, `--dry-run`, `--yes`, `-v`.

```bash
media-tool list [--full] [--external] [--include-virtual]
media-tool inspect DEVICE                  # pełna identyfikacja
media-tool serial|ids|uuids|partitions|mount-status|system-status|space|health DEVICE
media-tool benchmark DEVICE [--size-mib N] # prędkość odczytu: sekwencyjna + losowa 4 KiB
media-tool integrity DEVICE [--range 0:1M ...] [--samples 8] [--passes 2] [--seed N]
media-tool scan DEVICE [--start 0] [--length 1G]   # skan tylko do odczytu, mapa błędów
media-tool errors DEVICE                   # liczniki SMART + próbki odczytu
media-tool report --format json|csv|html|txt --output FILE [--device D] [--redact] [--overwrite]
media-tool report-diff STARY.json NOWY.json
media-tool hash FILE [--algo sha256]
media-tool compare IMAGE1 IMAGE2
media-tool image create DEVICE -o backup.img [--overwrite]
media-tool image resume DEVICE -o backup.img
media-tool image restore DEVICE --image backup.img   # ZAPIS: nadpisuje nośnik (wymaga wpisania NADPISZ)
media-tool image verify DEVICE --image backup.img
media-tool image inspect backup.img [--hash]
media-tool backup run DEVICE -o ~/kopie [--only zdjęcia,filmy] [--name N] [--volume PATH] [--no-verify]
media-tool backup run --source ~/Zdjęcia -o ~/kopie # kopia zwykłego folderu
media-tool backup verify ~/kopie/ETYKIETA          # czy kopia jest nienaruszona
media-tool backup list ~/kopie
media-tool unmount DEVICE                  # wymaga wpisanego potwierdzenia
media-tool eject DEVICE                    # odmontowanie + przygotowanie do odłączenia
media-tool dry-run image create DEVICE -o backup.img
media-tool config [show | get K | set K V | reset | path]
media-tool logs [-n 50] [--path]
media-tool features                        # co działa na tym systemie i dlaczego (nie)
media-tool diagnose                        # tryb diagnostyczny
```

`DEVICE`: ścieżka lub nazwa (`/dev/disk4`, `disk4`, `sdb`, `\\.\PHYSICALDRIVE1`, `PhysicalDrive1`),
albo `serial:…`, `wwn:…`, `uuid:…`.

**Kody wyjścia:** `0` – OK; `1` – błąd; `3` – wykryto problem/różnice (weryfikacja obrazu nie przeszła,
obrazy różne, skan/integralność z błędami, raporty różne); `130` – przerwano.

## Menu interaktywne

`media-tool` (bez argumentów, w terminalu) lub `media-tool menu` – kolorowe menu w pięciu grupach:

* **Nośniki i identyfikacja:** wykrywanie, pełna lista, identyfikacja, numery seryjne, identyfikatory sprzętowe,
  UUID, partycje, punkty montowania, status zamontowania i systemowy, wolne miejsce.
* **Kopie zapasowe:** kopia plików z karty/dysku, weryfikacja kopii, lista kopii, obraz całego nośnika
  (utwórz/wznów/zbadaj), porównanie obrazu z nośnikiem, **przywracanie obrazu**.
* **Diagnostyka (tylko odczyt):** SMART, test prędkości, test integralności, błędy odczytu, skan.
* **Raporty:** JSON, CSV, HTML, tekst, zmiany między raportami.
* **Narzędzia:** sumy kontrolne, porównanie obrazów, odmontowanie, przygotowanie do odłączenia, logi,
  konfiguracja, diagnostyka, tryb symulacji.

Przy każdej pozycji widać, czy jest dostępna na tym systemie: `✔` dostępne, `◐` częściowo, `✖` niedostępne
(z powodem). Wybór urządzenia pokazuje karty z nazwą, rozmiarem, typem, numerem seryjnym i plakietkami
(`SYSTEM`, `wymienny`, `zamontowany`).

**Wybór folderów i plików bez wpisywania:** przy każdej ścieżce (folder kopii, plik obrazu, miejsce raportu…) menu
otwiera **systemowe okno wyboru** – macOS: okno Findera, Windows: standardowe okno, Linux: `zenity` lub `kdialog`.
Można też wybrać folder domyślny albo wpisać ścieżkę. Anulowanie okna przerywa operację (nic nie jest zapisywane
w domyślnym miejscu bez Twojej wiedzy). Okna są wyłączone w sesji SSH i bez ekranu (wtedy pytanie o ścieżkę), a także
przez `MEDIA_TOOL_NO_GUI=1`.

Interfejs używa kolorów tylko w prawdziwym terminalu; `NO_COLOR=1` je wyłącza,
`MEDIA_TOOL_ASCII=1` zamienia ramki i symbole na zwykłe znaki.

## Co program pokazuje

Dla każdego urządzenia (gdy dostępne): nazwa, ścieżka, producent, model, oprogramowanie sprzętowe,
numery seryjne (urządzenia / kontrolera / karty SD / czytnika), USB VID/PID/revision, WWN, EUI-64,
UUID / PARTUUID, typ, magistrala i interfejs (USB/SATA/NVMe/SD/MMC), pojemność **nominalna** (z warstwy sprzętowej)
i **wykryta przez system**, rozmiary sektora i bloku, liczba sektorów, tablica partycji (GPT/MBR),
partycje (typ, system plików, etykieta, **UUID systemu plików**, **PARTUUID**, montowanie, szyfrowanie),
status zamontowania / tylko do odczytu / systemowy / wymienny, SMART (stan, temperatura, godziny pracy, błędy)
oraz data i godzina odczytu.

Rozróżniane są: numer seryjny urządzenia ≠ kontrolera ≠ czytnika ≠ karty; UUID systemu plików ≠ PARTUUID ≠
nazwa urządzenia ≠ etykieta woluminu ≠ identyfikator sprzętowy.

## Backup plików i całej karty

Są dwa różne rodzaje kopii – wybierasz w menu pozycję **Kopia zapasowa** albo odpowiednie polecenie:

| Chcesz… | Co robi | Polecenie | Administrator? |
|---|---|---|---|
| **Całą kartę / cały nośnik** | obraz 1:1: wszystkie partycje, tablica partycji, miejsce poza woluminami; można go potem przywrócić na kartę | `image create DEVICE -o karta.img` | tak (odczyt surowego urządzenia) |
| **Same pliki** (zdjęcia, filmy…) | pliki ze **wszystkich** zamontowanych woluminów karty; przyrostowo, tylko nowe | `backup run DEVICE -o FOLDER --volume all` | nie |

W menu program po wybraniu „całej karty” sam proponuje ponowne uruchomienie z uprawnieniami administratora
(`sudo` – zapyta o hasło w tym samym terminalu; Windows: okno UAC) i wraca dokładnie do tej funkcji, odmontowanie
karty przed kopią (obraz jest wtedy spójny) oraz weryfikację obrazu względem karty i bezpieczne wysunięcie po kopii.

`media-tool backup run DEVICE -o FOLDER` kopiuje **pliki** z zamontowanego woluminu karty/dysku do wskazanego folderu
(zdjęcia, filmy, dokumenty…). Nie wymaga uprawnień administratora.

* **Źródło jest tylko czytane** – nic na nim nie jest zmieniane ani usuwane. Cel nie może leżeć na kopiowanym nośniku.
* **Przyrostowo:** kolejne uruchomienia kopiują tylko nowe i zmienione pliki (reszta jest pomijana bez czytania).
  Przerwany backup wystarczy uruchomić ponownie.
* **Nic nie jest nadpisywane.** Aparaty po sformatowaniu karty numerują zdjęcia od nowa (`IMG_0001.JPG` ma inną
  zawartość niż ostatnio) – nowa wersja trafia obok jako `IMG_0001_<8 znaków SHA-256>.JPG`. Ten sam plik ze zmienioną
  tylko datą nie jest kopiowany drugi raz.
* **Weryfikacja:** każda kopia jest czytana ponownie z miejsca docelowego i porównywana sumą SHA-256 ze źródłem
  (`--no-verify` to wyłącza).
* **Filtry:** `--only zdjęcia,filmy` (też `photos,videos`); pomijane są pliki systemowe (`.Spotlight-V100`, `.Trashes`,
  `System Volume Information`, `.DS_Store`…).
* Przy kilku woluminach (`--volume all`, w menu domyślnie) każdy wolumin to osobny zestaw: `<model>-<numer>-<etykieta>`.
* Układ: `FOLDER/<etykieta karty>/…pliki…` oraz `.media-tool-backup/` z indeksem i dziennikiem każdego uruchomienia.
* `backup verify FOLDER/<zestaw>` sprawdza po czasie, czy kopia jest nienaruszona; `backup list FOLDER` pokazuje
  zestawy i uruchomienia. `backup run --source FOLDER -o …` kopiuje zwykły folder.
* `--dry-run` pokazuje, co zostałoby skopiowane, nic nie zapisując.

## Obrazy nośników

* `image create` czyta nośnik blokami, liczy sumę kontrolną „w locie" i zapisuje plik obrazu oraz
  `backup.img.mtinfo.json` (tożsamość źródła, postęp, nieczytelne zakresy, suma).
* **Nieczytelne sektory** są zapisywane jako zera i wpisywane na listę; po przekroczeniu limitu
  (`max_read_errors`) tworzenie się przerywa.
* **Wznawianie:** po przerwaniu (Ctrl+C, błąd, odłączenie) `image resume` kontynuuje od zapisanego miejsca – tylko
  jeśli to to samo urządzenie (numer seryjny / model / rozmiar), inaczej odmawia, żeby nie zmieszać dwóch nośników.
* `image verify` porównuje nośnik z obrazem bajt po bajcie (zapisane nieczytelne zakresy są pomijane).
* `image inspect` pokazuje metadane i **tablicę partycji odczytaną z samych danych** (GPT/MBR z kontrolą CRC,
  nagłówka zapasowego i ochronnego MBR).
* `compare` porównuje dwa obrazy (pierwsza różnica, liczba różnych bloków, sumy).
* **`image restore DEVICE --image FILE` – ZAPISUJE obraz na nośnik i nadpisuje wszystkie jego dane.** Najpierw
  odmawia, jeśli: to dysk systemowy lub nie da się tego ustalić, nośnik jest wewnętrzny lub tylko do odczytu, obraz jest
  większy od nośnika, niekompletny, ma niezgodną sumę kontrolną, nie ma metadanych (chyba że
  `--allow-image-without-metadata` dla zwykłych `.img`) albo leży na nośniku docelowym. Dopiero potem prosi o wpisanie
  ścieżki, numeru seryjnego i słowa `NADPISZ`, odmontowuje nośnik, zapisuje i **weryfikuje zapis odczytem zwrotnym**
  (niezgodność = kod wyjścia 3). Niedostępne na Windows w tej wersji.

## Dostępność funkcji na systemach

| Funkcja | macOS | Linux | Windows |
|---|---|---|---|
| Lista, identyfikacja, partycje, montowanie | `diskutil`, `system_profiler` | `lsblk`, sysfs, `udevadm` | PowerShell (`Get-Disk` …) |
| Numer seryjny karty SD (CID) | tylko jeśli podaje go system* | tak, dla natywnego slotu MMC (sysfs) | tylko jeśli podaje go system* |
| SMART | `smartctl`; bez niego status z `diskutil` | `smartctl` | `smartctl`; bez niego `Get-PhysicalDisk` |
| Odczyt surowy (obraz/skan/integralność/benchmark) | `/dev/rdiskN` (+sudo) | `/dev/…` (+sudo) | `\\.\PHYSICALDRIVEn` (+admin) |
| Odmontowanie / odłączenie | `diskutil unmountDisk/eject` | `udisksctl`/`umount`/`eject` | Shell *Eject* (tylko woluminy z literą) |

\* czytniki USB zwykle ukrywają dane karty; wtedy pole pozostaje `NIEDOSTĘPNE`.

## Stan testów i znane ograniczenia

* **166 testów automatycznych** (`python3 -m unittest discover -s tests -t .`): obrazowanie, wznawianie,
  uszkodzone sektory, weryfikacja, GPT, odczyty wyrównane do sektora (symulacja surowych urządzeń), backup
  (przyrostowość, kolizje nazw, błędy odczytu, przerwanie), przywracanie (głównie przypadki odmowy), zabezpieczenia
  i potwierdzenia, raporty, konfiguracja, CLI, interfejs. Zabezpieczenia sprawdzono też mutacyjnie (celowe psucie
  kodu musi wywalać testy). CI uruchamia je na Ubuntu, macOS i Windows (Python 3.9 i 3.13).
* **macOS – przetestowane na prawdziwym systemie** (dysk wewnętrzny oraz obrazy dysków `hdiutil` jako „karty"):
  lista, identyfikacja, skan, integralność, benchmark, obraz + weryfikacja + inspekcja (suma zgodna z `shasum`),
  odmontowanie z potwierdzeniem, **backup prawdziwej karty (przyrostowy, kolizje nazw) oraz prawdziwe przywracanie
  obrazu na drugi nośnik** – suma surowego urządzenia po zapisie identyczna z obrazem (`shasum`); **obraz całej
  karty z dwiema partycjami** (tablica GPT i obie partycje w obrazie, suma zgodna z `shasum`) oraz kopia plików
  z obu woluminów; systemowe okno wyboru folderu otwiera się (AppleScript) – wybór myszką sprawdziłem tylko do
  momentu otwarcia okna, nie klikałem w nim.
* **Linux i Windows – tylko na danych przykładowych** (parsery `lsblk`/PowerShell na przygotowanych danych).
  Backendy są napisane, ale **nie uruchamiałem ich na tych systemach ani na prawdziwej karcie SD**.
  Szczególnie do sprawdzenia: odczyt VID/PID i numeru seryjnego na Windows, Shell *Eject*, odczyt surowy
  `\\.\PHYSICALDRIVE` (wymaga odczytów wyrównanych do sektora – program tak czyta).
* Nie przetestowano kart SD w czytnikach USB ani natywnym slocie macOS: nazwy kluczy z `system_profiler`
  (`SPCardReaderDataType`) dla numeru seryjnego karty są nieznane – pełne surowe dane zapisują się w polu `extra`.
* Test prędkości omija pamięć podręczną systemu „w miarę możliwości" (macOS `F_NOCACHE`, Linux `posix_fadvise`);
  na Windows wynik może być zawyżony. Wynik jest orientacyjny.
* **Przywracanie obrazu** jest przetestowane tylko na macOS (obrazy dysków); na Linuksie napisane, ale
  **nietestowane na prawdziwym nośniku**, na Windows celowo wyłączone. Nie ma formatowania ani czyszczenia nośników.
* **Backup plików** przetestowany na prawdziwej karcie-obrazie na macOS i w CI na wszystkich trzech systemach
  (na plikach); nie na prawdziwej karcie SD w czytniku USB.
* **Pakiety do pobrania:** workflow GitHub Actions buduje i testuje paczki na Windows, Linux i macOS (arm64 + Intel)
  i publikuje wydania z sumami SHA-256. Lokalnie uruchamiałem binarkę macOS, `.pyz` i skrypty
  `Start-macOS.command` / `Start-Linux.sh` (z menu w prawdziwym terminalu). **`Start-Windows.bat` i binarki
  Windows/Linux nie były uruchamiane przeze mnie ani na prawdziwym urządzeniu** – CI sprawdza tylko `--version` i testy.
* **Instalatory:** `install.sh` przetestowałem na macOS na prawdziwym wydaniu z GitHuba (pobranie, brak kwarantanny,
  weryfikacja sumy – także podrobionego pliku, reinstalacja, odinstalowanie, odmowa nadpisania cudzych katalogów).
  **`install.ps1` ma zweryfikowaną tylko składnię – nie był uruchomiony na Windows**; instalator na Linuksie też nie.
* Python 3.9+ – testowane na 3.9.6, 3.12 i (w CI) 3.13.

## Rozwój

```
media_tool/
  backends/      macos.py linux.py windows.py base.py   # jedyne miejsce zależne od systemu
  models.py      Device / Partition / Health, odciski tożsamości
  safety.py      wybór urządzenia, potwierdzenia, ponowna weryfikacja
  imaging.py     obrazy, wznawianie, weryfikacja     rawio.py   odczyt surowy z obsługą błędów I/O
  scanning.py    integralność + skan                 benchmark.py  test prędkości
  partition_tables.py  MBR/GPT                       reports.py    JSON/CSV/HTML/TXT + diff
  backup.py      kopia plików (przyrostowa, z weryfikacją)
  commands.py    logika poleceń (wspólna dla CLI i menu)
  cli.py  menu.py  ui.py  config.py  logs.py  capabilities.py
tests/           testy (unittest, bez zależności)
docs/ARCHITEKTURA.md
```

Szczegóły architektury, formatu `.mtinfo.json` i dodawania backendu: [docs/ARCHITEKTURA.md](docs/ARCHITEKTURA.md).

> **Starsza wersja:** katalog `driveinfo/` i `drive_info.py` to poprzedni, prostszy eksporter (CSV/JSON, testy
> zapisu na woluminie). Jest zachowany bez zmian i może zostać usunięty po przejściu na `media-tool`.

## Licencja

[MIT](LICENSE)
