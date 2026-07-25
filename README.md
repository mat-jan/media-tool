# Eksport informacji o karcie SD do CSV / SD Card Info Exporter to CSV

Skrypt bash dla macOS, który odczytuje informacje o włożonej karcie SD z czytnika kart i eksportuje je do pliku CSV.

A bash script for macOS that reads information about an inserted SD card from the built-in card reader and exports it to a CSV file.

---

## Wymagania / Requirements

- macOS (wbudowany czytnik kart SD / built-in SD card reader)
- Bash (domyślnie dostępny / available by default)

---

## Użycie / Usage

```bash
chmod +x sd_card_export.sh
./sd_card_export.sh
```

---

## Dane wyjściowe / Output

Skrypt tworzy plik CSV z datą i godziną w nazwie, np. `sd_card_info_DATE_TIME.csv`.

The script creates a CSV file with a timestamp in the filename, e.g. `sd_card_info_DATE_TIME.csv`.

| Field | Example Value |
|-------|--------------|
| Export Date | DATE |
| Product Name | PN |
| Manufacturer ID | Sandisk etc. |
| Serial Number (hex) | SN (hex) |
| Serial Number (dec) | SN |
| Revision | NUMBER |
| Manufacturing Date | DATE |
| Specification Version | NUMBER |
| Capacity | XXXX GB |
| File System | ExFAT |
| Vendor ID | VENDOR ID |
| Device ID (PID) | PID |
| S.M.A.R.T. Status | Verified |
| BSD Name | disk4 |
| Mount Point | /Volumes/Untitled |

---

## Kod / Code

```bash
#!/bin/bash

# sd_card_export.sh
# Eksportuje informacje o karcie SD do pliku CSV

OUTPUT="sd_card_info_$(date +%Y%m%d_%H%M%S).csv"

echo "Szukam karty SD..."

RAW=$(system_profiler SPCardReaderDataType 2>/dev/null)

if [ -z "$RAW" ]; then
  echo "Błąd: brak danych z czytnika kart. Czy karta jest włożona?"
  exit 1
fi

# Wyciągnij pola
PRODUCT_NAME=$(echo "$RAW"    | grep "Product Name:"          | awk -F': ' '{print $2}' | xargs)
MANUFACTURER_ID=$(echo "$RAW" | grep "Manufacturer ID:"       | awk -F': ' '{print $2}' | xargs)
SERIAL_NUMBER=$(echo "$RAW"   | grep "Serial Number:"         | awk -F': ' '{print $2}' | xargs)
SERIAL_DEC=$(printf "%d" "$SERIAL_NUMBER" 2>/dev/null)
REVISION=$(echo "$RAW"        | grep "Revision:"              | head -1 | awk -F': ' '{print $2}' | xargs)
MFG_DATE=$(echo "$RAW"        | grep "Manufacturing Date:"    | awk -F': ' '{print $2}' | xargs)
SPEC_VER=$(echo "$RAW"        | grep "Specification Version:" | sed 's/.*Specification Version: //' | tr -d ',' | xargs)
CAPACITY=$(echo "$RAW"        | grep "Capacity:"              | head -1 | sed 's/.*Capacity: //' | xargs)
BSD_NAME=$(echo "$RAW"        | grep "BSD Name:"              | head -1 | awk -F': ' '{print $2}' | xargs)
FILESYSTEM=$(echo "$RAW"      | grep "File System:"           | awk -F': ' '{print $2}' | xargs)
VENDOR_ID=$(echo "$RAW"       | grep "Vendor ID:"             | head -1 | awk -F': ' '{print $2}' | xargs)
DEVICE_ID=$(echo "$RAW"       | grep "Device ID:"             | awk -F': ' '{print $2}' | xargs)
SMART=$(echo "$RAW"           | grep "S.M.A.R.T. status:"     | awk -F': ' '{print $2}' | xargs)
MOUNT=$(echo "$RAW"           | grep "Mount Point:"           | awk -F': ' '{print $2}' | xargs)
EXPORT_DATE=$(date "+%Y-%m-%d %H:%M:%S")

# Funkcja owijająca wartość w cudzysłowy (zabezpiecza przecinki i spacje)
csv() {
  echo "\"$1\",\"$2\""
}

# Zapisz CSV
{
  csv "Field" "Value"
  csv "Export Date"           "$EXPORT_DATE"
  csv "Product Name"          "$PRODUCT_NAME"
  csv "Manufacturer ID"       "$MANUFACTURER_ID"
  csv "Serial Number (hex)"   "$SERIAL_NUMBER"
  csv "Serial Number (dec)"   "$SERIAL_DEC"
  csv "Revision"              "$REVISION"
  csv "Manufacturing Date"    "$MFG_DATE"
  csv "Specification Version" "$SPEC_VER"
  csv "Capacity"              "$CAPACITY"
  csv "File System"           "$FILESYSTEM"
  csv "Vendor ID"             "$VENDOR_ID"
  csv "Device ID (PID)"       "$DEVICE_ID"
  csv "S.M.A.R.T. Status"     "$SMART"
  csv "BSD Name"              "$BSD_NAME"
  csv "Mount Point"           "$MOUNT"
} > "$OUTPUT"

echo "✅ Gotowe! Dane zapisane do: $OUTPUT"
echo ""
cat "$OUTPUT"
```

---

## Licencja / License

MIT