@echo off
setlocal

REM -------------------------------------------------
REM resolve repo root (parent of tools)
REM -------------------------------------------------
set SCRIPT_DIR=%~dp0
set ROOT_DIR=%SCRIPT_DIR%..

REM normalize paths
for %%I in ("%ROOT_DIR%") do set ROOT_DIR=%%~fI

set SRC=%ROOT_DIR%\tortoisehg\hgqt
set DST=%ROOT_DIR%\library\tortoisehg\hgqt

REM -------------------------------------------------
REM list of modified files (relative to hgqt)
REM -------------------------------------------------
set FILES= ^
blockmatcher.py ^
chunks.py ^
cmdui.py ^
docklog.py ^
filedata.py ^
filelistview.py ^
fileview.py ^
graph.py ^
lexers.py ^
merge.py ^
messageentry.py ^
qscilib.py ^
qtapp.py ^
qtlib.py ^
repofilter.py ^
repomodel.py ^
repoview.py ^
repowidget.py ^
revpanel.py ^
settings.py ^
status.py ^
theme.py ^
workbench.py

REM -------------------------------------------------
REM ensure destination exists
REM -------------------------------------------------
if not exist "%DST%" (
    mkdir "%DST%"
)

REM -------------------------------------------------
REM copy files + clean matching pyc
REM -------------------------------------------------
for %%F in (%FILES%) do (
    echo Copying %%F

    copy /Y "%SRC%\%%F" "%DST%\%%F" >nul
    if errorlevel 1 (
        echo ERROR copying %%F
        exit /b 1
    )

    REM legacy .pyc
    if exist "%DST%\%%~nF.pyc" (
        del "%DST%\%%~nF.pyc"
    )

    REM __pycache__ (Python 3.9)
    if exist "%DST%\__pycache__\%%~nF.cpython-39.pyc" (
        del "%DST%\__pycache__\%%~nF.cpython-39.pyc"
    )
)

echo apply_patch.bat OK
endlocal
exit /b 0
