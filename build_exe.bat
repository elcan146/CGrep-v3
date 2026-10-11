@echo off
echo ============================================
echo  SDR Collector - EXE Builder
echo ============================================

:: Install PyInstaller if not present
pip show pyinstaller >nul 2>&1 || pip install pyinstaller

:: Build the exe
:: --onedir keeps it as a folder (more reliable with native DLLs than --onefile)
:: --add-binary bundles the RTL-SDR DLLs
:: --hidden-import forces scipy/sqlalchemy submodules that PyInstaller misses
pyinstaller ^
    --clean ^
    --onedir ^
    --name sdr_collector ^
    --console ^
    --add-binary "librtlsdr.dll;." ^
    --add-binary "libgcc_s_seh-1.dll;." ^
    --add-binary "libiconv-2.dll;." ^
    --hidden-import scipy.signal ^
    --hidden-import scipy._lib.messagestream ^
    --hidden-import sqlalchemy.dialects.postgresql ^
    --hidden-import psycopg2 ^
    --hidden-import numpy ^
    sdr_collector.py

:: Copy config.env into the output folder so it's ready to edit
copy /Y config.env dist\sdr_collector\config.env

echo.
echo ============================================
echo  Build complete!
echo  Output folder: dist\sdr_collector\
echo.
echo  Files to give the remote laptop:
echo    dist\sdr_collector\   (entire folder)
echo.
echo  On the remote laptop:
echo    1. Edit config.env - set DB_HOST to your server IP
echo    2. Install Zadig USB driver for RTL-SDR dongle
echo    3. Run sdr_collector.exe
echo ============================================
pause
