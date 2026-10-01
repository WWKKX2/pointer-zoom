@echo off
cd /d "%~dp0"

set "PY="
call :try "C:\Python314\python.exe"
call :try "C:\Python313\python.exe"
call :try "C:\Python312\python.exe"
call :try "C:\Python311\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :try "%ProgramFiles%\Python314\python.exe"
call :try "%ProgramFiles%\Python313\python.exe"
if not defined PY (
  for /f "delims=" %%i in ('where python.exe 2^>nul') do call :try "%%i"
)

if not defined PY (
  echo.
  echo [ERROR] Need a Python 3 with tkinter.
  echo.
  echo Install Python 3 from https://www.python.org/downloads/
  echo and keep "tcl/tk and IDLE" checked during setup.
  echo.
  echo Already have Python? Run this to check:
  echo     python -c "import tkinter"
  echo.
  pause
  exit /b 1
)

set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%PY%"

start "" "%PYW%" "%~dp0src\pointer_gui.py" --config "%~dp0pointer-zoom.ini"
exit /b 0

:try
if defined PY exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "import tkinter" >nul 2>nul
if not errorlevel 1 set "PY=%~1"
exit /b 0
