@echo off
REM Launcher for parse_resumes.py (cron, every 15 min via Task Scheduler).
cd /d "%~dp0"
set PYTHONUTF8=1
python -X utf8 parse_resumes.py
