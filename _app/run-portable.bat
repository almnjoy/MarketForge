@echo off
REM ============================================================
REM  MARKET FORGE - PORTABLE DESK  (port 8410)
REM  Everything in one window: dashboard + embedded bot engine. No server,
REM  no Docker. Reads bot\.env, where STOCK_ENV decides paper vs live.
REM  First run: copy bot\.env.template to bot\.env and add your Alpaca keys.
REM  A second desk (remote engine) can run on 8411 at the same time.
REM ============================================================
cd /d %~dp0..
title MARKET FORGE - PORTABLE (8410)
set MF_PORT=8410
set MF_EMBEDDED=1
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -c "import flask" 2>nul || (echo Installing Flask... & %PY% -m pip install -r requirements.txt)
start "" http://localhost:8410
%PY% app.py
pause
