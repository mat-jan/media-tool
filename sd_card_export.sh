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
  csv "Export Date"          "$EXPORT_DATE"
  csv "Product Name"         "$PRODUCT_NAME"
  csv "Manufacturer ID"      "$MANUFACTURER_ID"
  csv "Serial Number (hex)"  "$SERIAL_NUMBER"
  csv "Serial Number (dec)"  "$SERIAL_DEC"
  csv "Revision"             "$REVISION"
  csv "Manufacturing Date"   "$MFG_DATE"
  csv "Specification Version" "$SPEC_VER"
  csv "Capacity"             "$CAPACITY"
  csv "File System"          "$FILESYSTEM"
  csv "Vendor ID"            "$VENDOR_ID"
  csv "Device ID (PID)"      "$DEVICE_ID"
  csv "S.M.A.R.T. Status"    "$SMART"
  csv "BSD Name"             "$BSD_NAME"
  csv "Mount Point"          "$MOUNT"
} > "$OUTPUT"

echo "✅ Gotowe! Dane zapisane do: $OUTPUT"
echo ""
cat "$OUTPUT"