# Drive Info

Narzędzie do sprawdzania nośników (karty SD, pendrive'y, SSD/HDD, NVMe) na macOS i Linuksie: informacje, eksport CSV/JSON, test podróbek, test prędkości, raport HTML/PDF.
Inspect and test storage devices on macOS and Linux: info, CSV/JSON export, fake-capacity detection, speed test, HTML/PDF report.

Trzy interfejsy / three interfaces: **CLI**, **TUI** (terminal), **GUI** (przeglądarka / browser).

## Wymagania / Requirements

- Python 3.8+ — rdzeń i GUI bez zależności / core and GUI have no dependencies
- macOS: `diskutil`, `system_profiler` · Linux: `lsblk`
- Opcjonalnie / optional: `smartctl` (status SMART), `textual` (TUI: `pip install textual`), Chrome/Chromium (raport PDF)

## Użycie / Usage

**Najprościej:** uruchom `./drive_info.py` bez argumentów – kreator w terminalu pyta, co chcesz zrobić:
wybierasz urządzenia, akcje (podgląd, eksport, test prędkości, test podróbek, raport), pola do eksportu,
format i nazwę pliku. Nic nie jest zapisywane ani testowane bez Twojego potwierdzenia.
*Simplest: run `./drive_info.py` with no arguments – a terminal wizard lets you pick devices, actions, export fields, format and file name; nothing is written without confirmation.*

Dla skryptów / for scripting:

```bash
./drive_info.py list                   # lista urządzeń / list devices
./drive_info.py export disk4           # CSV; też: --all, -e, -f json, -o plik|-
./drive_info.py speed disk4            # test prędkości + klasa karty (U1/U3/V30, A1/A2)
./drive_info.py capacity disk4         # test podróbek: zapis + weryfikacja całej wolnej pojemności
./drive_info.py report disk4 --speed --capacity -o raport.html   # lub .pdf
./drive_info.py tui                    # interfejs w terminalu
./drive_info.py gui                    # interfejs w przeglądarce (localhost)
```

Nośnik musi być zamontowany; testy używają jego punktu montowania (lub `--mount ŚCIEŻKA`).

## Bezpieczeństwo / Safety

- Testy zapisują tylko zwykłe pliki w ukrytym katalogu `.driveinfo_test` na zamontowanym woluminie i zawsze je usuwają. Nie dotykają surowego urządzenia ani istniejących plików.
- Odmawiają pracy na woluminie głównym `/`.
- Test pojemności zapełnia całą wolną przestrzeń — dla dużych kart trwa długo (`--limit-mb` testuje tylko początek).
- GUI słucha wyłącznie na `127.0.0.1` i wymaga losowego tokena z adresu wypisanego przy starcie.
- Wyniki prędkości są szacunkowe (przez system plików), nie stanowią certyfikacji.

## Pola eksportu / Export fields

`device, name, model, vendor, serial, serial_dec, firmware, size_bytes, size, bus, kind, removable, internal, filesystem, partitions, mount_points, smart, manufacturer_id, mfg_date, vendor_id, product_id`

Pola niedostępne zostają puste / unavailable fields stay empty.

## Struktura / Layout

`driveinfo/collect.py` (wykrywanie / discovery) · `tests.py` (testy / tests) · `report.py` · `cli.py` · `tui.py` · `web.py`

## Licencja / License

MIT
