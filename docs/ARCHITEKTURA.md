# Architektura media-tool

## Warstwy

```
cli.py / menu.py          interfejs (argparse, menu 1-32)         ← tylko parsowanie i prezentacja
        │
commands.py               logika poleceń, wspólna dla CLI i menu  ← Context(backend, console, cfg, dry_run, as_json)
        │
 ┌──────┼───────────────┬────────────────┬──────────────┐
safety  imaging/rawio   scanning/benchmark  reports      config/logs/capabilities
 │
backends/*                macOS · Linux · Windows (jedyny kod zależny od systemu)
```

Reguły zależności: `backends` nie zna reszty aplikacji poza `models`; `commands` jest jedyną warstwą, która
łączy backend, bezpieczeństwo i operacje; `cli`/`menu` nie zawierają logiki biznesowej.

## Model danych (`models.py`)

`Device` (+ `Partition`, `Health`) – brak informacji to `None`. Dopiero `to_dict(display=True)` zamienia `None`
na `NIEDOSTĘPNE` (raporty, JSON, tekst). `device_from_dict` robi odwrotnie, więc raporty można wczytać z powrotem.

Odciski (hash) używane przez zabezpieczenia:

| Odcisk | Zawiera | Użycie |
|---|---|---|
| `identity()` | ścieżka, producent, model, numery seryjne, WWN, VID/PID, rozmiar, tablica i lista partycji | operacje odczytu |
| `state()` | identity + punkty montowania | operacje zmieniające stan (ściślejsze) |
| `stable_identity()` | jak identity, **bez ścieżki** | wznawianie obrazu (system mógł nadać nowy `diskN`) |

## Backend

Interfejs (`backends/base.py`): `list_devices(include_virtual)`, `raw_path(device)`, `read_health(device)`,
`unmount(device)`, `power_off(device)`.

Dodanie systemu/źródła danych:
1. Napisz klasę dziedziczącą po `Backend`, zwracającą `Device` – wypełniaj **tylko to, co masz dowód, że system
   podał**; resztę zostaw `None`.
2. Numery seryjne: `serial` = to, co system przypisuje urządzeniu; `reader_serial` ustawiaj tylko, gdy masz dowód,
   że należy do czytnika/mostka (np. udev `ID_DRIVE_FLASH_SD`); `card_serial` tylko z rejestru karty (CID).
   Gdy numer pochodzi z USB, dodaj notatkę ostrzegawczą do `device.notes`.
3. Zarejestruj w `backends/__init__.py` i dodaj parser z testem na przykładowych danych narzędzia.

## Przepływ operacji i zabezpieczenia (`Context.pick` + `safety.py`)

```
resolve_device(spec)           ścieżka/nazwa/serial:/wwn:/uuid: ; niejednoznaczne → odmowa
   │
   ├─ risk=None (metadane)     → zwróć urządzenie
   ├─ dry-run                  → pokaż urządzenie, bez potwierdzeń
   ├─ READ / FILE_WRITE        → show_device + t/n   (--yes pomija; tryb nieinteraktywny bez --yes → odmowa)
   └─ DEVICE_STATE             → show_device + ścieżka + nr seryjny + POTWIERDZAM (bez obejścia; dysk systemowy → odmowa)
   │
revalidate()                   ponowny odczyt; zmiana odcisku → DeviceChanged (przerwanie)
   │
operacja + audit w logu
```

`Risk.DEVICE_WRITE` używa wyłącznie `image restore` (jedyny zapis na nośnik). Droga: `pre_check(d)` (dysk systemowy /
nieustalony → odmowa, wewnętrzny → odmowa, tylko-do-odczytu → odmowa, kontrola obrazu przez `plan_restore`) →
`confirm_typed(word="NADPISZ")` → `revalidate(strict)` → odmontowanie → `revalidate(identity)` → `restore_image`
(sprawdzenie sumy obrazu przed zapisem, zapis, `fsync`, **obowiązkowa weryfikacja odczytem zwrotnym**).

## Backup plików (`backup.py`)

Czyta pliki z zamontowanego woluminu (nie z surowego urządzenia – bez uprawnień administratora) i zapisuje do
`<cel>/<nazwa>/`. `index.json` (ścieżka względna → lista wersji `{size, mtime_ns, sha256, dest}`) pozwala pomijać
niezmienione pliki bez czytania. Dla zmienionego pliku: kopia do `.part` z liczeniem SHA-256 w locie → ta sama suma
co zapisana wersja = dedup (zmieniła się tylko data) → inaczej zapis pod nazwą pierwotną lub, gdy zajęta,
`<nazwa>_<sha8>.<rozszerzenie>` (nigdy nadpisanie) → odczyt zwrotny i porównanie sum (`--no-verify` wyłącza).
Dziennik każdego uruchomienia: `.media-tool-backup/runs/*.json`.

## Interfejs (`ui.py`, `menu.py`)

`ui.py` to jedyne miejsce znające kody ANSI: `paint`/`good`/`problem`, tabele wyrównywane według szerokości
widocznej, ramki z zawijaniem (ostrzeżeń nie obcinamy), pasek postępu z ETA. Kolory tylko w terminalu
(`NO_COLOR`, `MEDIA_TOOL_COLOR`, `MEDIA_TOOL_ASCII`); w testach i potokach wynik jest czystym tekstem.

## Odczyt surowy (`rawio.py`)

`RawReader` otwiera ścieżkę **tylko `rb`**, próbuje ominąć cache, mapuje błędy uprawnień na zrozumiały komunikat.
`read_resilient` przy błędzie I/O czyta sektor po sektorze, nieczytelne zastępuje zerami i zapisuje zakres
(`[offset, długość]`, scalane). Używane przez obrazowanie, weryfikację, skan i test integralności.

## Format `.mtinfo.json` (obok obrazu)

```json
{
  "format": 1, "tool": "media-tool", "tool_version": "0.1.0",
  "created_at": "…", "updated_at": "…", "finished_at": "…", "duration_s": 12.3,
  "source": {"path": "/dev/disk4", "identity": {"vendor": …, "model": …, "serial": …, "size": …},
             "fingerprint": "…", "vendor": …, "model": …, "serial": …, "size_bytes": …, "sector_size": 512},
  "image":  {"size_bytes": …, "chunk_size": …, "sector_size": 512, "hash_algorithm": "sha256", "hash": "…|null"},
  "progress": {"bytes_done": …, "complete": true},
  "bad_ranges": [[offset, length], …], "read_errors": 0
}
```

Zapis atomowy (plik tymczasowy + `os.replace`); stan zapisywany co 64 MiB oraz przy każdym przerwaniu/błędzie.
`hash` jest wypełniony dopiero dla kompletnego obrazu.

## Raporty (`reports.py`)

`build_snapshot` → słownik (z opcjonalnym maskowaniem numerów seryjnych) → `render(snapshot, fmt)`.
CSV neutralizuje komórki zaczynające się od `= + - @` (wstrzykiwanie formuł), HTML escapuje wszystkie wartości.
`diff_reports` dopasowuje urządzenia po numerze seryjnym/WWN/ścieżce i porównuje spłaszczone pola, pomijając
zmienne (data odczytu, temperatura).

## Konfiguracja i logi

* Konfiguracja: macOS `~/Library/Application Support/media-tool`, Linux `~/.config/media-tool`,
  Windows `%APPDATA%\media-tool`; log z rotacją w odpowiednim katalogu logów. `MEDIA_TOOL_HOME` nadpisuje oba
  (używane w testach).
* Niepoprawne wartości w pliku konfiguracji są ignorowane (wracają domyślne), nigdy nie blokują programu.

## Testy

`python3 -m unittest discover -s tests -t .` – sztuczne „urządzenia" to zwykłe pliki (`tests/helpers.py`), a
`FakeBackend` pozwala testować CLI end-to-end, w tym uszkodzone sektory (podmiana `RawReader`), przerwanie i
wznowienie, zmianę urządzenia po potwierdzeniu i wszystkie ścieżki potwierdzeń.
