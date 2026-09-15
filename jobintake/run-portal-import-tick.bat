@echo off
REM Launcher for portal_import_tick.py (daemon, self-loops with --loop).
cd /d "%~dp0"
set PYTHONUTF8=1
python -X utf8 portal_import_tick.py --loop
