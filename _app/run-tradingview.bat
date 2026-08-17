@echo off
REM ============================================================
REM  TRADINGVIEW, steerable by Market Forge
REM
REM  Opens Chrome with the DevTools protocol on :9223 so the desk (and the
REM  copilot) can change symbol, change timeframe, and screenshot the chart.
REM
REM  A SEPARATE user-data-dir is deliberate: without it Chrome just hands the
REM  URL to your already-running browser and ignores the debug flag entirely.
REM  This also keeps it out of the way of your normal windows and extensions.
REM  First run: log in to TradingView once in this window.
REM ============================================================
cd /d %~dp0..
title TRADINGVIEW (CDP :9223)

set CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" (
  echo Could not find chrome.exe. Set CHROME= by hand in this file.
  pause & exit /b 1
)

start "" "%CHROME%" ^
  --remote-debugging-port=9223 ^
  --user-data-dir="%LOCALAPPDATA%\MarketForge\tv-profile" ^
  --new-window "https://www.tradingview.com/chart/"

echo.
echo TradingView launching with CDP on http://localhost:9223
echo Verify:  curl http://localhost:9223/json/version
REM NOTE: ">" is a redirect operator in batch - "-^>" or it writes a file named "ask"
echo Desk:    http://localhost:8410  -^>  ask the copilot to pull up a chart
echo.
