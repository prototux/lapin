@echo off
rem Runs Lapin from wherever this folder is: the first run creates .venv here
rem with the dependencies; the app itself runs from this folder's sources.
setlocal
set "DIR=%~dp0"
set "DIR=%DIR:~0,-1%"
set "VENV=%DIR%\.venv"
if not exist "%VENV%\Scripts\python.exe" (
    py -3 -m venv "%VENV%" || python -m venv "%VENV%" || exit /b 1
    "%VENV%\Scripts\python.exe" -m pip install -q --upgrade pip
    "%VENV%\Scripts\python.exe" -m pip install -q -r "%DIR%\requirements.txt" || exit /b 1
)
set "PYTHONPATH=%DIR%;%PYTHONPATH%"
set "LAPIN_LAUNCHER=%DIR%\lapin.cmd"
if "%~1"=="" (
    start "" "%VENV%\Scripts\pythonw.exe" -m lapin_desktop
) else (
    "%VENV%\Scripts\python.exe" -m lapin_desktop %*
)
