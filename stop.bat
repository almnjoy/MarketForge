@echo off
REM ============================================================
REM  MARKET FORGE - FULL STOP
REM
REM  Closing the console window usually stops everything, but on Windows the
REM  engine is a child process and atexit does not reliably fire on window
REM  close - so it can survive with no window attached. This kills anything
REM  listening on the desk ports and any leftover engine, then verifies.
REM
REM  IMPORTANT: this does NOT touch your broker. Trailing stops live at Alpaca
REM  and keep working whether this machine is on or off. Stopping the desk
REM  never leaves a position unprotected.
REM ============================================================
cd /d %~dp0
title MARKET FORGE - STOP

echo Stopping Market Forge...
echo.

for %%P in (8410 8411 8796) do (
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr /R /C:"LISTENING" ^| findstr ":%%P "') do (
    echo   killing PID %%A on port %%P
    taskkill /T /F /PID %%A >nul 2>nul
  )
)

if exist "bot\data\engine.pid" (
  set /p ENGPID=<bot\data\engine.pid
  if defined ENGPID (
    echo   killing recorded engine PID %ENGPID%
    taskkill /T /F /PID %ENGPID% >nul 2>nul
  )
  del /q "bot\data\engine.pid" >nul 2>nul
)

timeout /t 2 /nobreak >nul
echo.
echo === still listening? (nothing below = fully stopped) ===
netstat -ano | findstr /R /C:"LISTENING" | findstr ":8410 :8411 :8796"
echo.
echo === stray python? (check these are not the engine) ===
tasklist /FI "IMAGENAME eq python.exe" 2>nul | findstr python.exe
echo.
echo Desk stopped. Your broker-side stops are untouched and still working.
pause
