@echo off
rem media-tool - uruchomienie jednym kliknieciem (Windows). Nie wymaga instalowania zadnych pakietow.
rem Kolejnosc: gotowy media-tool.exe obok tego pliku -> media-tool.pyz -> katalog zrodlowy media_tool\
rem Raporty i obrazy trafiaja domyslnie do %USERPROFILE%\media-tool (zmiana: zmienna MEDIA_TOOL_OUTPUT_DIR).
rem Plik jest celowo w czystym ASCII (bez polskich znakow) - cmd.exe bywa na nie wrazliwy.
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1
title media-tool

set "HERE=%~dp0"
if defined MEDIA_TOOL_OUTPUT_DIR (set "OUT=%MEDIA_TOOL_OUTPUT_DIR%") else (set "OUT=%USERPROFILE%\media-tool")
if not exist "%OUT%" mkdir "%OUT%" >nul 2>&1
cd /d "%OUT%" 2>nul || cd /d "%USERPROFILE%"

rem --- argumenty (--elevated = uruchomiono ponownie jako administrator)
set "ELEVATED=0"
set "ARGS="
:collect
if "%~1"=="" goto collected
if /i "%~1"=="--elevated" (set "ELEVATED=1") else (set ARGS=!ARGS! %1)
shift
goto collect
:collected

rem --- czy mamy uprawnienia administratora?
set "ISADMIN=0"
net session >nul 2>&1
if %errorlevel%==0 set "ISADMIN=1"

rem --- wybor programu do uruchomienia
set "RUN="
if exist "%HERE%media-tool.exe" set RUN="%HERE%media-tool.exe"
if defined RUN goto have_run

set "PYCMD="
for %%P in ("py -3" "python" "python3") do (
  if not defined PYCMD (
    %%~P -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
    if !errorlevel!==0 set "PYCMD=%%~P"
  )
)
if not defined PYCMD goto no_python
if exist "%HERE%media-tool.pyz" (
  set RUN=%PYCMD% "%HERE%media-tool.pyz"
) else if exist "%HERE%media_tool\__main__.py" (
  set "PYTHONPATH=%HERE:~0,-1%"
  set RUN=%PYCMD% -m media_tool
)
if not defined RUN goto no_python

:have_run
rem --- wybor trybu (tylko przy starcie bez argumentow i bez uprawnien administratora)
if "%ARGS%"=="" if "%ISADMIN%"=="0" if "%ELEVATED%"=="0" (
  echo media-tool - wybierz tryb pracy:
  echo   1^) Podstawowy - informacje o nosnikach ^(bez uprawnien administratora^)
  echo   2^) Pelny - dodatkowo obrazy, skan, test integralnosci i predkosci ^(Windows poprosi o zgode^)
  set "CH=1"
  set /p "CH=Wybor [1]: "
  if "!CH!"=="2" (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '--elevated' -Verb RunAs" >nul 2>&1
    if errorlevel 1 (echo Nie udalo sie uzyskac uprawnien administratora. & pause & exit /b 1)
    exit /b 0
  )
  echo.
)
echo Raporty i obrazy zapisuje w: %OUT%
echo.
%RUN%%ARGS%
set "CODE=%errorlevel%"
echo.
pause
exit /b %CODE%

:no_python
echo Nie znaleziono Pythona 3.9+ ani gotowego programu media-tool.exe.
echo.
echo   * Pobierz wersje bez Pythona (archiwum z plikiem media-tool.exe) ze strony Releases, albo
echo   * zainstaluj Pythona:  winget install Python.Python.3.12   (lub https://www.python.org/downloads/)
echo     W instalatorze zaznacz "Add python.exe to PATH".
echo.
pause
exit /b 1
