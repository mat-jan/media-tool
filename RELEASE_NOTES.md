## media-tool – wersja beta

Pierwsza publiczna wersja nowego, modularnego narzędzia do identyfikacji, diagnostyki, raportowania i obrazowania
nośników danych (macOS, Linux, Windows). Wszystkie operacje są domyślnie **tylko do odczytu**; program nie zapisuje na nośnik.

### Instalacja (bez ostrzeżeń systemu, bez administratora)

**macOS / Linux** (Terminal):
```bash
curl -fsSL https://raw.githubusercontent.com/mat-jan/media-tool/main/install.sh | sh
```
**Windows** (PowerShell):
```powershell
irm https://raw.githubusercontent.com/mat-jan/media-tool/main/install.ps1 | iex
```
Pliki pobrane tymi komendami nie dostają znacznika „z internetu", więc macOS Gatekeeper i Windows SmartScreen
nie pokazują ostrzeżenia. Instalator sprawdza sumę SHA-256 z `SHA256SUMS.txt`.

### Albo pobierz archiwum i kliknij dwukrotnie skrypt startowy

| Plik do pobrania | Dla kogo | Skrypt |
|---|---|---|
| `media-tool-windows-x64.zip` | Windows 10/11 | `Start-Windows.bat` |
| `media-tool-macos-arm64.zip` | Mac z Apple Silicon | `Start-macOS.command` |
| `media-tool-macos-x64.zip` | Mac z Intel | `Start-macOS.command` |
| `media-tool-linux-x64.zip` | Linux x86-64 | `Start-Linux.sh` |
| `media-tool-universal.zip` | każdy system z Pythonem 3.9+ | wszystkie trzy skrypty |

Przy pobraniu z przeglądarki program nie jest podpisany certyfikatem, więc system pokaże ostrzeżenie:
* **macOS:** kliknij **Done** (nie „Move to Trash"), potem *Ustawienia systemowe → Prywatność i ochrona → „Otwórz mimo to"*
  albo w Terminalu `xattr -dr com.apple.quarantine <rozpakowany-katalog>`. („Prawy klik → Otwórz" nie działa od macOS 15.)
* **Windows:** SmartScreen → „Więcej informacji" → „Uruchom mimo to".

### ⚠ To jest beta – co nie było sprawdzone
- **Windows i Linux** zostały przetestowane tylko na danych przykładowych i w CI; **nie na prawdziwych urządzeniach**.
- **Prawdziwe karty SD** (natywny slot i czytniki USB) nie były testowane; numer seryjny karty bywa niedostępny.
- `Start-Windows.bat` i podniesienie uprawnień (UAC) są zaimplementowane, ale nie zostały uruchomione na Windows.
- Operacje na nośniku (obraz, skan) wymagają uprawnień administratora.

Zgłaszaj problemy w Issues – szczególnie z Windows, Linux i kartami SD w czytnikach.

---
