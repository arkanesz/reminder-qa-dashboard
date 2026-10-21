@echo off
cd /d "%~dp0"

:menu
echo.
echo ====================================
echo   QA Reminder ^& Report
echo ====================================
echo   1. Kirim Reminder QA
echo   2. Generate Report Ready for QA
echo   3. Cek Tiket Mangkrak ^(^> 24 Jam^)
echo   0. Keluar
echo ====================================
set /p PILIHAN="Pilih opsi (0/1/2/3): "

if "%PILIHAN%"=="1" (
    python send_reminder.py
) else if "%PILIHAN%"=="2" (
    python generate_report.py
) else if "%PILIHAN%"=="3" (
    goto stale_menu
) else if "%PILIHAN%"=="0" (
    goto end
) else (
    echo Pilihan tidak valid, coba lagi.
    goto menu
)

echo.
pause
goto menu

:stale_menu
echo.
echo ------------------------------------
echo   Cek Tiket Mangkrak - Pilih Status
echo ------------------------------------
echo   1. Ready For QA
echo   2. Pending QA
echo   3. Code Review
echo   4. Checking
echo   5. Semua Status
echo   0. Kembali ke menu utama
echo ------------------------------------
set /p STATUSPILIHAN="Pilih status (0-5): "

if "%STATUSPILIHAN%"=="1" (
    python check_stale_tickets.py "Ready For QA"
) else if "%STATUSPILIHAN%"=="2" (
    python check_stale_tickets.py "Pending QA"
) else if "%STATUSPILIHAN%"=="3" (
    python check_stale_tickets.py "Code Review"
) else if "%STATUSPILIHAN%"=="4" (
    python check_stale_tickets.py "Checking"
) else if "%STATUSPILIHAN%"=="5" (
    python check_stale_tickets.py
) else if "%STATUSPILIHAN%"=="0" (
    goto menu
) else (
    echo Pilihan tidak valid, coba lagi.
    goto stale_menu
)

echo.
pause
goto menu

:end
echo Sampai jumpa!
timeout /t 2 >nul
