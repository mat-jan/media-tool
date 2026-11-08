#!/usr/bin/env bash
# media-tool – uruchomienie jednym kliknięciem (Linux). Nie wymaga instalowania żadnych pakietów
# (potrzebny Python 3.9+ i narzędzie lsblk z pakietu util-linux – są w każdej popularnej dystrybucji).
# Kolejność: gotowy program "media-tool" obok tego pliku -> media-tool.pyz -> katalog źródłowy media_tool/.
# Raporty i obrazy trafiają domyślnie do ~/media-tool (zmiana: MEDIA_TOOL_OUTPUT_DIR).
# Menedżer plików: "Uruchom w terminalu". Z terminala: ./Start-Linux.sh   (albo: bash Start-Linux.sh)

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${MEDIA_TOOL_OUTPUT_DIR:-$HOME/media-tool}"
mkdir -p "$OUT" 2>/dev/null && cd "$OUT" || cd "$HOME"

pause() { echo; read -r -p "Naciśnij Enter, aby zamknąć..." _; }

RUN=()
if [ -x "$HERE/media-tool" ]; then
  RUN=("$HERE/media-tool")
else
  for PY in python3 python; do
    if command -v "$PY" >/dev/null 2>&1 &&
       "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
      if [ -f "$HERE/media-tool.pyz" ]; then RUN=("$PY" "$HERE/media-tool.pyz"); break; fi
      if [ -d "$HERE/media_tool" ]; then
        export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
        RUN=("$PY" -m media_tool); break
      fi
    fi
  done
fi

if [ ${#RUN[@]} -eq 0 ]; then
  echo "Nie znaleziono Pythona 3.9+ ani gotowego programu 'media-tool'."
  echo
  echo "  Debian/Ubuntu:  sudo apt install python3"
  echo "  Fedora:         sudo dnf install python3"
  echo "  Arch:           sudo pacman -S python"
  echo "  Albo pobierz wersję bez Pythona (archiwum z plikiem 'media-tool') ze strony Releases."
  pause; exit 1
fi

command -v lsblk >/dev/null 2>&1 || echo "UWAGA: brak polecenia lsblk (pakiet util-linux) – lista nośników nie zadziała."

ELEV=0
if [ $# -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
  echo "media-tool – wybierz tryb pracy:"
  echo "  1) Podstawowy – informacje o nośnikach (bez hasła administratora)"
  echo "  2) Pełny – dodatkowo obrazy, skan, test integralności i prędkości (sudo, poprosi o hasło)"
  read -r -p "Wybór [1]: " CH
  [ "$CH" = "2" ] && ELEV=1
  echo
fi
echo "Raporty i obrazy zapisuję w: $OUT"
echo

if [ "$ELEV" = "1" ]; then
  sudo env "HOME=$HOME" "PYTHONPATH=${PYTHONPATH:-}" "${RUN[@]}" "$@"
else
  "${RUN[@]}" "$@"
fi
CODE=$?
pause
exit $CODE
