## media-tool – wersja beta

Pierwsza publiczna wersja nowego, modularnego narzędzia do identyfikacji, diagnostyki, raportowania i obrazowania
nośników danych (macOS, Linux, Windows). Wszystkie operacje są domyślnie **tylko do odczytu**; program nie zapisuje na nośnik.

### Jak używać (bez instalowania pakietów)
Pobierz paczkę dla swojego systemu, rozpakuj i **kliknij dwukrotnie** skrypt startowy:

| Plik do pobrania | Dla kogo | Skrypt |
|---|---|---|
| `media-tool-windows-x64.zip` | Windows 10/11 | `Start-Windows.bat` |
| `media-tool-macos-arm64.zip` | Mac z Apple Silicon | `Start-macOS.command` |
| `media-tool-macos-x64.zip` | Mac z Intel (jeśli dostępny) | `Start-macOS.command` |
| `media-tool-linux-x64.zip` | Linux x86-64 | `Start-Linux.sh` |
| `media-tool-universal.zip` | każdy system z Pythonem 3.9+ | wszystkie trzy skrypty |

Pierwsze uruchomienie: program nie jest podpisany – Windows SmartScreen („Więcej informacji → Uruchom mimo to"),
macOS Gatekeeper (prawy klik → Otwórz). Szczegóły w README.

### ⚠ To jest beta – co nie było sprawdzone
- **Windows i Linux** zostały przetestowane tylko na danych przykładowych i w CI; **nie na prawdziwych urządzeniach**.
- **Prawdziwe karty SD** (natywny slot i czytniki USB) nie były testowane; numer seryjny karty bywa niedostępny.
- `Start-Windows.bat` i podniesienie uprawnień (UAC) są zaimplementowane, ale nie zostały uruchomione na Windows.
- Operacje na nośniku (obraz, skan) wymagają uprawnień administratora.

Zgłaszaj problemy w Issues – szczególnie z Windows, Linux i kartami SD w czytnikach.

---
