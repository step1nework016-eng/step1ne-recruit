@echo off
REM Launcher for report_tick.py (cron, every 5 min via Task Scheduler).
cd /d "%~dp0"
set PYTHONUTF8=1
python -X utf8 report_tick.py
