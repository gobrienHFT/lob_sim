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
echo   Options MM Walkthrough Mode
echo   Concise run, case brief, and clear artifact order
echo =============================================================

where python >nul 2>nul
if errorlevel 1 goto missing_python

python -c "import matplotlib; import lob_sim.cli" >nul 2>nul
if errorlevel 1 goto missing_deps

echo [options] Scenario: %SCENARIO%
echo [options] Steps: %STEPS%
echo [options] Seed: %SEED%
echo [options] Output folder: %OUT_DIR%
echo.

python -u -m lob_sim.cli options-demo --out-dir "%OUT_DIR%" --steps %STEPS% --seed %SEED% --scenario %SCENARIO% --brief --walkthrough-mode
if errorlevel 1 goto fail

echo.
echo [options] Recommended artifact order:
echo [options]   1. %OUT_DIR%\case_brief.md
echo [options]   2. %OUT_DIR%\overview_dashboard.png
echo [options]   3. %OUT_DIR%\implied_vol_surface_snapshot.png
echo [options]   4. %OUT_DIR%\position_surface_heatmap.png
echo [options]   5. %OUT_DIR%\vega_surface_heatmap.png
echo [options]   6. representative fill in %OUT_DIR%\case_brief.md
echo [options]   7. docs\sample_outputs\scenario_matrix_seed7\scenario_matrix.md
echo [options]   8. docs\sample_outputs\toxicity_spread_sensitivity_seed7\toxicity_spread_sensitivity.md
echo [options] Open %OUT_DIR%\case_brief.md first.
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
