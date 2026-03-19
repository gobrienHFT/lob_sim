@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
call "%~dp0run_options_mm_case.bat" %*
