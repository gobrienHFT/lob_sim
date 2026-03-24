@echo off
setlocal EnableExtensions EnableDelayedExpansion

for %%I in ("%~dp0..\..") do set "ROOT_DIR=%%~fI"
cd /d "%ROOT_DIR%"

set "OUT_DIR=outputs"
set "SCENARIO=toxic_flow"
set "STEPS=180"
set "SEED=7"

if not "%~1"=="" set "SCENARIO=%~1"
if not "%~2"=="" set "OUT_DIR=%~2"
if not "%~3"=="" set "STEPS=%~3"
if not "%~4"=="" set "SEED=%~4"

echo =============================================================
echo   Options MM Quick Run
echo   Fast preset, concise metrics, clean artifact pack
echo =============================================================

where python >nul 2>nul
if errorlevel 1 goto missing_python

python -c "import matplotlib; import lob_sim.cli" >nul 2>nul
if errorlevel 1 goto missing_deps

echo [options] Scenario: %SCENARIO%
echo [options] Steps: %STEPS%
echo [options] Output folder: %OUT_DIR%
echo.

python -u -m lob_sim.cli options-demo --out-dir "%OUT_DIR%" --steps %STEPS% --seed %SEED% --scenario %SCENARIO% --brief
if errorlevel 1 goto fail

echo.
echo [options] Clean files written to %OUT_DIR%.
echo [options] Start with demo_report.md, then fills.csv, then pnl_timeseries.csv.
set "EXIT_CODE=0"
goto end

:missing_python
echo [options] Python was not found on PATH.
set "EXIT_CODE=1"
goto end

:missing_deps
echo [options] Python dependencies are missing.
echo [options] Run: pip install -r requirements.txt
set "EXIT_CODE=1"
goto end

:fail
echo [options] Launch failed. See the messages above.
set "EXIT_CODE=1"

:end
echo.
pause
exit /b %EXIT_CODE%
