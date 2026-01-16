@echo off
setlocal

set SCRIPT_DIR=%~dp0
set ROOT_DIR=%SCRIPT_DIR%..
for %%I in ("%ROOT_DIR%") do set ROOT_DIR=%%~fI

set LIB_DIR=%ROOT_DIR%\library
set ZIP_PATH=%ROOT_DIR%\library.zip

set SEVENZIP=C:\Program Files\7-Zip\7z.exe
if not exist "%SEVENZIP%" set SEVENZIP=C:\Program Files (x86)\7-Zip\7z.exe

if not exist "%SEVENZIP%" (
    echo ERROR: 7-Zip not found
    exit /b 1
)

echo [pack] Verifying Python stdlib layout

if not exist "%LIB_DIR%\encodings\__init__.pyc" (
    echo ERROR: repo\library is not a complete Python stdlib
    echo Missing: encodings\__init__.pyc
    echo.
    echo The library directory was probably not initialized
    echo from the original TortoiseHg library.zip.
    exit /b 1
)

echo [pack] repo\library looks OK
echo.

if exist "%ZIP_PATH%" (
    del "%ZIP_PATH%"
)

echo [pack] Creating library.zip

pushd "%LIB_DIR%"
"%SEVENZIP%" a -tzip -r -y "%ZIP_PATH%" *
if errorlevel 1 (
    popd
    echo ERROR: Failed to create library.zip
    exit /b 1
)
popd

echo [pack] library.zip created successfully
endlocal
exit /b 0
