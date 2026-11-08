## media-tool – wersja beta

Narzędzie do identyfikacji, diagnostyki, raportowania, **kopii zapasowych** i obrazowania nośników danych
(macOS, Linux, Windows). Operacje są domyślnie **tylko do odczytu**; jedyny zapis na nośnik to przywracanie obrazu
(`image restore`) – za podwójnym potwierdzeniem (`NADPISZ`), tylko na nośnikach zewnętrznych, z weryfikacją zapisu.

### Nowości w 1.0.0-beta.3
- **Backup plików** z karty/dysku: przyrostowy, bez nadpisywania (powtórzone nazwy zdjęć z aparatu trafiają obok
  z przyrostkiem), weryfikowany sumą SHA-256, bez uprawnień administratora; `backup verify` / `backup list`.
- **Przywracanie obrazu na nośnik** (macOS, Linux; nie Windows): wiele blokad bezpieczeństwa, weryfikacja po zapisie.
- **Nowy wygląd terminala:** kolory, ramki, grupowane menu, paski postępu z czasem do końca, karty urządzeń.
  Wyłączanie kolorów: `NO_COLOR=1`.

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
