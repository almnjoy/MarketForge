@echo off
cd /d %~dp0
title STOCKS//LOCAL
where py >nul 2>nul && (py -3 app.py) || (python app.py)
pause
