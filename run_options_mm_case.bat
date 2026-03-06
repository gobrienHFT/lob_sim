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
echo [options] Live talk track:
echo [options]   1. fair value from Black-Scholes on a skewed vol surface
echo [options]   2. reservation price shifts quotes as delta and vega inventory build
echo [options]   3. spread widens with realized vol and gamma risk
echo [options]   4. toxic flow tests adverse selection via one-step markout
echo [options]   5. spot hedges fire only when portfolio delta breaches the threshold
echo [options] Repo guide: docs\options_mm_demo_guide.md
python -u -m lob_sim.cli options-demo --out-dir "%OUT_DIR%" --steps %STEPS% --seed %SEED% --verbose --progress-every %PROGRESS_EVERY%
if errorlevel 1 goto fail

echo [options] Finished. Open %OUT_DIR% in Excel for options_mm_summary.csv, options_mm_config.csv, options_mm_path.csv, and options_mm_trades.csv.
echo [options] Open %OUT_DIR%\options_mm_walkthrough.md for the run-specific explanation.
set "EXIT_CODE=0"
goto end

:fail
echo [options] Command failed. Check output above.
set "EXIT_CODE=1"
goto end

:end
echo.
pause
exit /b %EXIT_CODE%
