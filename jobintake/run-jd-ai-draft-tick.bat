@echo off
REM Launcher for jd_ai_draft_tick.py (cron, every 90 sec via Task Scheduler).
cd /d "%~dp0"
set PYTHONUTF8=1
python -X utf8 jd_ai_draft_tick.py
