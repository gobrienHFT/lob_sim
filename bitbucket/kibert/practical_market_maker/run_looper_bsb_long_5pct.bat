@echo off
cd /d "%~dp0"
:loop
python ldo_roe_loop_bot.py --live --confirm-live I_UNDERSTAND_THIS_CAN_LIQUIDATE --symbol BSBUSDT --side LONG --position-side BOTH --margin-type ISOLATED --leverage 3 --max-min-notional-leverage 10 --adaptive-min-notional-leverage --allocation-pct 0.90 --fee-buffer-pct 0.01 --take-profit-mode fixed-roe --take-profit-roe 0.05 --stop-loss-roe 0.90 --emergency-stop-roe 0.35 --entry-mode market --safety-recent-vol-minutes 5 --safety-max-adverse-vol-ratio 1.10 --safety-liquidity-window-seconds 12 --safety-liquidity-samples 3 --safety-max-depth-drop-pct 35 --safety-max-spread-widen-multiple 2
echo Bot process exited. Restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
