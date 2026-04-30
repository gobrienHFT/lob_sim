@echo off
cd /d "%~dp0"
python ldo_roe_loop_bot.py --live --confirm-live I_UNDERSTAND_THIS_CAN_LIQUIDATE --take-profit-mode min-viable --scan-profile liquidity --leverage 2
pause
