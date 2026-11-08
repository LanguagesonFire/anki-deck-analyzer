@echo off
setlocal enabledelayedexpansion
title Anki Deck Analyzer
cd /d "%~dp0"

rem Drag an .apkg file onto this file, or double-click it and type a path.

where py >nul 2>nul && (set PY=py) || (set PY=python)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 (
  echo Python 3.9 or newer was not found.
  echo.
  echo Install it from https://www.python.org/downloads/ and tick
  echo "Add Python to PATH" during setup, then run this again.
  echo.
  pause
  exit /b 1
)

rem Install into a private environment the first time, and again whenever
rem this folder holds a new version, so nothing else on the machine is touched.
set WANT=
for /f "tokens=2 delims==" %%v in ('findstr /b /c:"version = " pyproject.toml') do set WANT=%%~v
set WANT=%WANT: =%
set WANT=%WANT:"=%
set HAVE=
if exist ".venv\installed-version.txt" set /p HAVE=<".venv\installed-version.txt"
if not exist ".venv\Scripts\anki-deck-analyzer.exe" set HAVE=
if not "%HAVE%"=="%WANT%" (
  echo Setting up version %WANT%. This happens once and takes a few minutes.
  if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv || (echo Could not create the environment. & pause & exit /b 1)
  )
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet ".[lemma,chinese,japanese]"
  if errorlevel 1 (
    echo.
    echo The install failed. Check the internet connection and run this again.
    echo If it keeps failing, delete the .venv folder here and try once more.
    pause
    exit /b 1
  )
  >".venv\installed-version.txt" echo %WANT%
  echo Done.
  echo.
)

if "%ANTHROPIC_API_KEY%"=="" (
  if exist "api-key.txt" (
    set /p ANTHROPIC_API_KEY=<api-key.txt
  ) else (
    echo An Anthropic API key is needed. Create one at
    echo https://console.anthropic.com/settings/keys after adding credit under Billing.
    echo.
    call :askkey
    if "!ANTHROPIC_API_KEY!"=="" (echo No key given. & pause & exit /b 1)
    >api-key.txt echo !ANTHROPIC_API_KEY!
    echo.
    echo Saved to api-key.txt in this folder. Delete that file to change it.
    echo.
  )
)

set "DECK=%~1"
if "%DECK%"=="" set /p "DECK=Drag the .apkg file into this window and press Enter: "
if "%DECK%"=="" (echo No deck given. & pause & exit /b 1)
rem A path copied from Explorer or dropped into the window comes in quotes.
set "DECK=%DECK:"=%"
if not exist "%DECK%" (echo File not found: !DECK! & pause & exit /b 1)

for %%f in ("%DECK%") do set "NAME=%%~nf"
set "REPORT=%~dp0%NAME% - report.html"
".venv\Scripts\anki-deck-analyzer.exe" "%DECK%" -o "%REPORT%"

echo.
if exist "%REPORT%" (
  echo Opening the report...
  start "" "%REPORT%"
)
pause
exit /b 0

:askkey
set "ANTHROPIC_API_KEY="
set /p "ANTHROPIC_API_KEY=Paste your key here and press Enter: "
if "!ANTHROPIC_API_KEY!"=="" exit /b 0
set "ANTHROPIC_API_KEY=!ANTHROPIC_API_KEY: =!"
if not "!ANTHROPIC_API_KEY:~0,7!"=="sk-ant-" (
  echo That doesn't look like an Anthropic key: they start with sk-ant-. Try again.
  goto askkey
)
exit /b 0
