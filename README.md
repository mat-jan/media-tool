# media-tool

Narzędzie CLI do **identyfikacji, diagnostyki, raportowania, obrazowania i weryfikacji nośników danych**:
dysków, pendrive'ów, kart SD, czytników kart. Działa na **macOS, Linux i Windows**, w czystym Pythonie
(3.9+, bez zależności).

**Zasada nadrzędna:** użytkownik sam wybiera, co chce zrobić. Wszystko jest **tylko do odczytu**,
dopóki wyraźnie nie wybierzesz innej operacji – a nawet wtedy program nigdy nie zapisuje na nośnik.

## Spis treści
- [Szybki start – bez instalowania pakietów](#szybki-start--bez-instalowania-pakietów)
- [Bezpieczeństwo](#bezpieczeństwo)
- [Polecenia](#polecenia)
- [Menu interaktywne](#menu-interaktywne)
- [Co program pokazuje](#co-program-pokazuje)
- [Obrazy nośników](#obrazy-nośników)
- [Dostępność funkcji na systemach](#dostępność-funkcji-na-systemach)
- [Stan testów i znane ograniczenia](#stan-testów-i-znane-ograniczenia)
- [Rozwój](#rozwój)

## Szybki start – bez instalowania pakietów

Pobierz archiwum ze strony **Releases**, rozpakuj i **kliknij dwukrotnie** skrypt dla swojego systemu.
Skrypt zapyta, czy pracować w trybie podstawowym (tylko informacje), czy pełnym (obrazy, skan, testy – poprosi
o hasło/zgodę administratora), i uruchomi menu.

| System | Plik do kliknięcia | Co jest potrzebne |
|---|---|---|
| Windows | `Start-Windows.bat` | nic, jeśli pobrałeś paczkę `windows-x64` (zawiera `media-tool.exe`); w paczce `universal`: Python 3.9+ |
| macOS | `Start-macOS.command` | nic, jeśli pobrałeś paczkę `macos-arm64` / `macos-x64`; w paczce `universal`: Python 3.9+ (jest w „Narzędziach wiersza poleceń" Apple) |
| Linux | `Start-Linux.sh` (menedżer plików: „Uruchom w terminalu") | nic dodatkowego w paczce `linux-x64`; w `universal`: Python 3.9+; `lsblk` z pakietu `util-linux` (jest wszędzie) |

Paczki: `media-tool-<system>.zip` = jeden samodzielny program (bez Pythona) + skrypt startowy;
`media-tool-universal.zip` = jeden plik `media-tool.pyz` (62 KiB) + skrypty dla wszystkich systemów.
Skrypt startowy sam wybiera, co uruchomić: gotowy program → `media-tool.pyz` → katalog źródłowy `media_tool/`
(więc działa też po pobraniu samego kodu jako ZIP z GitHuba). Raporty i obrazy trafiają domyślnie do
`~/media-tool` (`%USERPROFILE%\media-tool`; zmiana: zmienna `MEDIA_TOOL_OUTPUT_DIR`). Pliki utworzone przez `sudo`
są przekazywane zwykłemu użytkownikowi.

**Pierwsze uruchomienie – ostrzeżenia systemu** (program nie jest podpisany certyfikatem):
* **Windows SmartScreen:** „Więcej informacji" → „Uruchom mimo to".
* **macOS Gatekeeper:** prawy klik na pliku → „Otwórz" → „Otwórz" (lub `xattr -dr com.apple.quarantine <katalog>`).
  Jeśli bit wykonywalny zginął przy rozpakowaniu: `bash Start-macOS.command`.

Możesz też uruchamiać bez skryptów:

```bash
python3 media-tool.pyz                # menu (potrzebny tylko Python 3.9+)
python3 -m media_tool list            # z katalogu repozytorium, bez instalacji
./media-tool list                     # samodzielna binarka
pip install .                         # opcjonalnie: polecenie `media-tool`
```

Odczyt **surowych urządzeń** (obrazowanie, skan, test integralności, test prędkości) wymaga uprawnień
administratora (`sudo …` / terminal „jako administrator") – skrypty startowe robią to za Ciebie po wybraniu trybu
pełnego. Informacje o urządzeniach (`list`, `inspect`, `serial`, …) działają bez nich.
Opcjonalnie: `smartctl` (pakiet *smartmontools*) – pełne dane SMART.

Paczki buduje GitHub Actions (`.github/workflows/build.yml`): testy na Ubuntu/macOS/Windows i Pythonie 3.9 oraz 3.13,
potem PyInstaller na każdym systemie; przy tagu `v*` powstaje Release. Lokalnie: `python3 tools/build_pyz.py`.

## Bezpieczeństwo

| Poziom | Operacje | Wymagane potwierdzenie |
|---|---|---|
| Metadane systemu | `list`, `inspect`, `serial`, `ids`, `uuids`, `partitions`, `mount-status`, `system-status`, `space`, `health` | brak (to tylko odczyt informacji od systemu) |
| Odczyt nośnika | `benchmark`, `integrity`, `scan`, `errors`, `image create/resume/verify` | pokazanie danych urządzenia + `t/n` (`--yes` pomija pytanie) |
| Zmiana stanu | `unmount`, `eject` | wpisanie **pełnej ścieżki**, **numeru seryjnego** (jeśli jest) i słowa **`POTWIERDZAM`**; `--yes` **nie działa** |
| Zapis na nośnik | – | **program nie wykonuje takich operacji** |

* Urządzenia nigdy nie wybiera się „po cichu" tylko po nazwie: przed każdą operacją na nośniku widzisz producenta,
  model, numer seryjny, pojemność, ścieżkę, typ, status montowania, czy jest systemowy, partycje i punkty montowania.
  Można też wybrać po cechach: `serial:XYZ`, `wwn:…`, `uuid:…`. Niejednoznaczny wybór jest odrzucany.
* Po potwierdzeniu dane urządzenia są **odczytywane ponownie**; jeśli się zmieniły (inny numer seryjny, rozmiar,
  partycje, zamontowanie…) operacja jest przerywana.
* **Dysk systemowy** – odmontowanie/odłączenie zablokowane bezwarunkowo; operacje odczytu tylko z ostrzeżeniem.
* Nośnik jest otwierany wyłącznie do odczytu. Plik obrazu **nie może leżeć na obrazowanym nośniku** (sprawdzane
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
media-tool image verify DEVICE --image backup.img
media-tool image inspect backup.img [--hash]
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

`media-tool` (bez argumentów, w terminalu) lub `media-tool menu` – 32 pozycje prowadzące krok po kroku:

1 szybkie wykrywanie · 2 pełna lista · 3 identyfikacja · 4 numery seryjne · 5 identyfikatory sprzętowe ·
6 UUID/FS · 7 partycje · 8 punkty montowania · 9 zamontowanie · 10 dysk systemowy · 11 SMART ·
12 test prędkości · 13 integralność · 14 obraz (utwórz/wznów/zbadaj) · 15 weryfikacja obrazu · 16 sumy kontrolne ·
17 porównanie obrazów · 18–21 raporty JSON/CSV/HTML/TXT · 22 zmiany między raportami · 23 wolne miejsce ·
24 błędy odczytu · 25 skanowanie · 26 odmontowanie · 27 przygotowanie do odłączenia · 28 logi · 29 konfiguracja ·
30 diagnostyka · 31 tryb symulacji (przełącznik) · 32 wyjście.

Przy każdej pozycji widać, czy jest dostępna na tym systemie: `✔` dostępne, `◐` częściowo, `✖` niedostępne
(z powodem).

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

* **86 testów automatycznych** (`python3 -m unittest discover -s tests -t .`): obrazowanie, wznawianie,
  uszkodzone sektory, weryfikacja, GPT, odczyty wyrównane do sektora (symulacja surowych urządzeń),
  zabezpieczenia i potwierdzenia, raporty, konfiguracja, CLI. Przechodzą na Pythonie 3.9.6 i 3.12.
* **macOS – przetestowane na prawdziwym systemie** (dysk wewnętrzny oraz utworzony na czas testu obraz dysku
  `hdiutil`): lista, identyfikacja, skan, integralność, benchmark, obraz + weryfikacja + inspekcja (suma zgodna
  z `shasum`), odmontowanie z potwierdzeniem.
* **Linux i Windows – tylko na danych przykładowych** (parsery `lsblk`/PowerShell na przygotowanych danych).
  Backendy są napisane, ale **nie uruchamiałem ich na tych systemach ani na prawdziwej karcie SD**.
  Szczególnie do sprawdzenia: odczyt VID/PID i numeru seryjnego na Windows, Shell *Eject*, odczyt surowy
  `\\.\PHYSICALDRIVE` (wymaga odczytów wyrównanych do sektora – program tak czyta).
* Nie przetestowano kart SD w czytnikach USB ani natywnym slocie macOS: nazwy kluczy z `system_profiler`
  (`SPCardReaderDataType`) dla numeru seryjnego karty są nieznane – pełne surowe dane zapisują się w polu `extra`.
* Test prędkości omija pamięć podręczną systemu „w miarę możliwości" (macOS `F_NOCACHE`, Linux `posix_fadvise`);
  na Windows wynik może być zawyżony. Wynik jest orientacyjny.
* Nie ma (celowo) operacji zapisu na nośnik: formatowania, przywracania obrazu, czyszczenia.
* **Pakiety do pobrania:** `.pyz` i binarkę macOS (arm64) zbudowałem i uruchomiłem lokalnie (także z ograniczonym
  `PATH` i bez Pythona w środowisku); skrypty `Start-macOS.command` / `Start-Linux.sh` sprawdziłem na macOS
  (łącznie z menu w prawdziwym terminalu). **`Start-Windows.bat`, binarki Windows/Linux/macOS-Intel i workflow GitHub
  Actions nie były uruchamiane** – składnia PowerShella została zweryfikowana parserem, workflow tylko pod kątem YAML.
* Python 3.9+ – testowane na 3.9.6 i 3.12.

## Rozwój

```
media_tool/
  backends/      macos.py linux.py windows.py base.py   # jedyne miejsce zależne od systemu
  models.py      Device / Partition / Health, odciski tożsamości
  safety.py      wybór urządzenia, potwierdzenia, ponowna weryfikacja
  imaging.py     obrazy, wznawianie, weryfikacja     rawio.py   odczyt surowy z obsługą błędów I/O
  scanning.py    integralność + skan                 benchmark.py  test prędkości
  partition_tables.py  MBR/GPT                       reports.py    JSON/CSV/HTML/TXT + diff
  commands.py    logika poleceń (wspólna dla CLI i menu)
  cli.py  menu.py  ui.py  config.py  logs.py  capabilities.py
tests/           testy (unittest, bez zależności)
docs/ARCHITEKTURA.md
```

Szczegóły architektury, formatu `.mtinfo.json` i dodawania backendu: [docs/ARCHITEKTURA.md](docs/ARCHITEKTURA.md).

> **Starsza wersja:** katalog `driveinfo/` i `drive_info.py` to poprzedni, prostszy eksporter (CSV/JSON, testy
> zapisu na woluminie). Jest zachowany bez zmian i może zostać usunięty po przejściu na `media-tool`.

## Licencja

MIT
