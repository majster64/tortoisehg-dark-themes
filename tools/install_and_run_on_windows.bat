@echo off
title TortoiseHg Dark Theme - Install and Run
setlocal EnableDelayedExpansion
color 07

echo.
echo TortoiseHg Dark Theme - Install and Run (Windows)
echo.

set SCRIPT_DIR=%~dp0
set ROOT_DIR=%SCRIPT_DIR%..
for %%I in ("%ROOT_DIR%") do set ROOT_DIR=%%~fI

set THG_LIB=C:\Program Files\TortoiseHg\lib
set SRC_ZIP=%THG_LIB%\library.zip
set BAK_ZIP=%ROOT_DIR%\library.zip.backup
set LIB_DIR=%ROOT_DIR%\library

set SEVENZIP=C:\Program Files\7-Zip\7z.exe
if not exist "%SEVENZIP%" set SEVENZIP=C:\Program Files (x86)\7-Zip\7z.exe

if not exist "%SEVENZIP%" goto error

echo Checking administrator privileges...
net session >nul 2>&1
if %errorlevel%==0 (
    set IS_ADMIN=1
    echo Running as administrator
) else (
    set IS_ADMIN=0
    echo NOT running as administrator
)
echo.

echo [0] Backup original library.zip
if exist "%SRC_ZIP%" (
    if exist "%BAK_ZIP%" (
        echo Backup already exists - skipping
    ) else (
        echo Creating backup...
        copy "%SRC_ZIP%" "%BAK_ZIP%" >nul
        if errorlevel 1 goto error
    )
) else (
    echo No existing library.zip found
)
echo.

echo [0b] Initialize repo\library if needed

set NEED_RECREATE=0

if not exist "%LIB_DIR%\encodings\__init__.pyc" (
    set NEED_RECREATE=1
)

if "%NEED_RECREATE%"=="1" (
    echo repo\library is invalid or incomplete

    if exist "%LIB_DIR%" (
        echo Removing invalid repo\library
        rmdir /s /q "%LIB_DIR%"
    )

    mkdir "%LIB_DIR%"

    set ORIG_ZIP=
    if exist "%BAK_ZIP%" (
        set ORIG_ZIP=%BAK_ZIP%
    ) else (
        set ORIG_ZIP=%SRC_ZIP%
    )

    if not exist "!ORIG_ZIP!" (
        echo ERROR: Source library.zip not found
        goto error
    )

    echo Extracting original library.zip from:
    echo   !ORIG_ZIP!

    "%SEVENZIP%" x "!ORIG_ZIP!" -o"%LIB_DIR%" -y
    if errorlevel 1 goto error

    if not exist "%LIB_DIR%\encodings\__init__.pyc" (
        echo ERROR: Extracted library is still invalid
        goto error
    )

    echo repo\library successfully initialized
) else (
    echo repo\library already valid - skipping extract
)
echo.

echo [1] Applying patched Python files
call "%SCRIPT_DIR%apply_patch.bat"
if errorlevel 1 goto error
echo.

echo [2] Packing library.zip
call "%SCRIPT_DIR%pack_library_to_zip.bat"
if errorlevel 1 goto error
echo.

echo [3] Installing library.zip into TortoiseHg
if "%IS_ADMIN%"=="1" (
    copy /Y "%ROOT_DIR%\library.zip" "%THG_LIB%\" >nul
    if errorlevel 1 goto error
    echo Installed into Program Files
) else (
    echo ADMIN RIGHTS REQUIRED
    echo Installation into Program Files was NOT performed.
    echo.
    echo The file was created successfully:
    echo   %ROOT_DIR%\library.zip
    echo.
    echo Please manually copy it to:
    echo   %THG_LIB%
    goto done_no_install
)
echo.

echo [4] Starting TortoiseHg
start "" "C:\Program Files\TortoiseHg\thgw.exe"

:done
color 0A
echo.
echo DONE - Installation completed successfully
echo.
pause
exit /b 0

:done_no_install
color 0E
echo.
echo DONE - library.zip was created, but NOT installed
echo.
pause
exit /b 0

:error
color 0C
echo.
echo ERROR - Installation failed
echo.
pause
exit /b 1
