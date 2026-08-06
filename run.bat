@echo off
REM ============================================================
REM  MARKET FORGE - LIVE DESK  (port 8411)
REM  Talks to a REMOTE engine (config.json bot_base), which holds the
REM  REAL Alpaca keys. This is the real-money view.
REM  The portable/paper desk is 8410 - both can be open at once.
REM ============================================================
cd /d %~dp0
title MARKET FORGE - LIVE (8411)
set MF_PORT=8411
set MF_EMBEDDED=
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
start "" http://localhost:8411
%PY% app.py
pause
