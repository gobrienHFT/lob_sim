@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
set "OUT_DIR=data\options_demo"
set "STEPS=450"
set "SEED=7"
set "PROGRESS_EVERY=25"

if not "%~1"=="" set "OUT_DIR=%~1"
if not "%~2"=="" set "STEPS=%~2"
if not "%~3"=="" set "SEED=%~3"
if not "%~4"=="" set "PROGRESS_EVERY=%~4"

echo [options] Running options MM case study
python -u -m lob_sim.cli options-demo --out-dir "%OUT_DIR%" --steps %STEPS% --seed %SEED% --verbose --progress-every %PROGRESS_EVERY%
if errorlevel 1 goto fail

echo [options] Finished. Inspect %OUT_DIR% for summary, CSVs, and report PNG.
exit /b 0

:fail
echo [options] Command failed. Check output above.
exit /b 1
