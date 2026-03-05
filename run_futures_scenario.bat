@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
set "ENV_FILE=.env"
set "PROGRESS_EVERY=5000"

if not "%~2"=="" set "PROGRESS_EVERY=%~2"
if "%~1"=="" goto collect
set "FILE=%~1"
goto replay

:collect
echo [futures] No replay file supplied. Collecting fresh market data...
python -u -m lob_sim.cli --env "%ENV_FILE%" collect --verbose
if errorlevel 1 goto fail
for /f "delims=" %%F in ('powershell -NoProfile -Command "Get-ChildItem -Path data -Filter 'raw_*.ndjson*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName"') do set "FILE=%%F"
if not defined FILE goto nofile

:replay
echo [futures] Replaying %FILE%
python -u -m lob_sim.cli --env "%ENV_FILE%" replay --file "%FILE%" --verbose --progress-every %PROGRESS_EVERY%
if errorlevel 1 goto fail

echo [futures] Simulating %FILE%
python -u -m lob_sim.cli --env "%ENV_FILE%" simulate --file "%FILE%" --verbose --progress-every %PROGRESS_EVERY%
if errorlevel 1 goto fail

echo [futures] Finished. Inspect data\outputs for JSON and CSV outputs.
exit /b 0

:nofile
echo [futures] Unable to locate replay file in data\raw_*.ndjson*
exit /b 2

:fail
echo [futures] Command failed. Check output above.
exit /b 1
