@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM  MARKET FORGE - FULL STOP
REM
REM  Closing the console window usually stops everything, but on Windows the
REM  engine is a child process and atexit does not reliably fire on window
REM  close - so it can survive with no window attached. This kills anything
REM  listening on the desk ports and any leftover engine, then verifies.
REM
REM  A stop that is ALREADY ARMED lives at the broker and keeps working whether
REM  this machine is on or off. But an entry that has not filled yet is a
REM  different story: the watcher that arms its stop runs INSIDE this process.
REM  Stop the desk with a buy still working and the fill lands naked at the
REM  broker until someone reopens the app. So this refuses to stop while
REM  anything is in flight.
REM
REM  Override:  stop.bat --force
REM ============================================================
cd /d %~dp0..
title MARKET FORGE - STOP

if /I "%~1"=="--force" goto :dostop

echo Checking whether anything is still working...
set MFSAFE=UNKNOWN
for /f "usebackq delims=" %%R in (`powershell -NoProfile -Command ^
  "try{$r=Invoke-RestMethod -Uri 'http://localhost:8410/api/bot/shutdown-check' -TimeoutSec 8; if($r.safe){'SAFE'}else{'BLOCKED'}}catch{'UNKNOWN'}"`) do set MFSAFE=%%R

if /I "!MFSAFE!"=="BLOCKED" (
  echo.
  echo   ============================================================
  echo    STOP REFUSED - something is still working
  echo   ============================================================
  powershell -NoProfile -Command ^
    "try{$r=Invoke-RestMethod -Uri 'http://localhost:8410/api/bot/shutdown-check' -TimeoutSec 8; $r.reasons ^| ForEach-Object { '     - ' + $_ }}catch{}"
  echo.
  echo    The exit guarantee only holds while the desk is RUNNING. Leave it
  echo    open until the entry fills and its stop is armed, then stop.
  echo.
  echo    Really stop anyway:   stop.bat --force
  echo.
  pause
  exit /b 1
)

if /I "!MFSAFE!"=="UNKNOWN" echo   (desk not responding - nothing to ask, continuing)
if /I "!MFSAFE!"=="SAFE"    echo   nothing in flight, safe to stop.

:dostop
echo.
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
    echo   killing recorded engine PID !ENGPID!
    taskkill /T /F /PID !ENGPID! >nul 2>nul
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
echo Desk stopped. Stops that were already armed are at the broker and untouched.
pause
