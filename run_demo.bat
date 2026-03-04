@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

if "%~1"=="" goto collect
set "FILE=%~1"
goto run

:collect
echo [1/4] Collecting fresh data...
python -m lob_sim.cli collect
if errorlevel 1 goto fail
for /f "delims=" %%F in ('powershell -NoProfile -Command "Get-ChildItem -Path data -Filter 'raw_*.ndjson*' | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName"') do set "FILE=%%F"
if not defined FILE goto nofile

:run
echo [2/4] Replay on %FILE%
python -m lob_sim.cli replay --file "%FILE%"
if errorlevel 1 goto fail

echo [3/4] Simulate on %FILE%
python -m lob_sim.cli simulate --file "%FILE%"
if errorlevel 1 goto fail

echo [4/4] Writing recruiter summary.md
python write_summary_md.py --out summary.md
if errorlevel 1 goto fail

echo [DONE] Created summary.md from latest simulation output.
exit /b 0

:nofile
echo Unable to locate replay file in data\raw_*.ndjson*
exit /b 2

:fail
echo Command failed. Check output above.
exit /b 1
