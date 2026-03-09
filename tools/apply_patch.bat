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
bookmark.py ^
blockmatcher.py ^
chunks.py ^
cmdui.py ^
csinfo.py ^
docklog.py ^
filedata.py ^
filedialogs.py ^
filelistview.py ^
fileview.py ^
graph.py ^
grep.py ^
guess.py ^
hgignore.py ^
lexers.py ^
merge.py ^
messageentry.py ^
mq.py ^
qscilib.py ^
qtapp.py ^
qtlib.py ^
rejects.py ^
repoview.py ^
repofilter.py ^
repomodel.py ^
reporegistry.py ^
repoview.py ^
repowidget.py ^
resolve.py ^
revdetails.py ^
revpanel.py ^
settings.py ^
status.py ^
sync.py ^
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
