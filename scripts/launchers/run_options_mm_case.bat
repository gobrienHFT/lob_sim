@echo off
setlocal EnableExtensions EnableDelayedExpansion

for %%I in ("%~dp0..\..") do set "ROOT_DIR=%%~fI"
cd /d "%ROOT_DIR%"

set "OUT_DIR=outputs"
set "STEPS=360"
set "SEED=7"
set "PROGRESS_EVERY=60"
set "SCENARIO=calm_market"
set "LOG_MODE=compact"

if not "%~1"=="" set "OUT_DIR=%~1"
if not "%~2"=="" set "STEPS=%~2"
if not "%~3"=="" set "SEED=%~3"
if not "%~4"=="" set "PROGRESS_EVERY=%~4"
if not "%~5"=="" set "SCENARIO=%~5"
if not "%~6"=="" set "LOG_MODE=%~6"

call :banner
call :check_python
if errorlevel 1 goto fail
call :check_dependencies
if errorlevel 1 goto fail

echo [options] Launching case study...
echo [options] Scenario: %SCENARIO%
echo [options] Steps: %STEPS%
echo [options] Seed: %SEED%
echo [options] Output folder: %OUT_DIR%
echo [options] Guide: docs\options_mm_demo_guide.md
echo [options] Artifact flow: terminal summary -> demo_report.md -> fills.csv -> pnl_timeseries.csv -> pnl_over_time.png
echo.

python -u -m lob_sim.cli options-demo --out-dir "%OUT_DIR%" --steps %STEPS% --seed %SEED% --scenario %SCENARIO% --verbose --progress-every %PROGRESS_EVERY% --log-mode %LOG_MODE%
if errorlevel 1 goto fail

echo.
echo [options] Run complete.
echo [options] Recommended artifact order:
echo [options]   1. %OUT_DIR%\demo_report.md
echo [options]   2. %OUT_DIR%\fills.csv
echo [options]   3. %OUT_DIR%\pnl_timeseries.csv
echo [options]   4. %OUT_DIR%\checkpoints.csv
echo [options]   5. %OUT_DIR%\pnl_over_time.png
set "EXIT_CODE=0"
goto end

:banner
echo ==============================================================
echo   Options Market Making Case Study
echo   Black-Scholes, quote skew, toxic flow, hedging, PnL
echo ==============================================================
exit /b 0

:check_python
where python >nul 2>nul
if errorlevel 1 (
    echo [options] Python was not found on PATH.
    echo [options] Install Python and retry.
    exit /b 1
)
exit /b 0

:check_dependencies
python -c "import matplotlib; import lob_sim.cli" >nul 2>nul
if errorlevel 1 (
    echo [options] Python dependencies are missing.
    echo [options] Run: pip install -r requirements.txt
    exit /b 1
)
exit /b 0

:fail
echo.
echo [options] Launch failed. See the messages above.
set "EXIT_CODE=1"

:end
echo.
pause
exit /b %EXIT_CODE%
