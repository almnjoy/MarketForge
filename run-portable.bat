@echo off
REM MARKET FORGE - portable/friend edition: dashboard + embedded bot engine,
REM one window, no Docker. First run: copy bot\.env.template to bot\.env and
REM add your Alpaca PAPER keys.
cd /d %~dp0
title MARKET FORGE (embedded)
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -c "import flask" 2>nul || (echo Installing Flask... & %PY% -m pip install -r requirements.txt)
set MF_EMBEDDED=1
%PY% app.py
pause
