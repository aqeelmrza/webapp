@echo off
echo ============================================
echo   Aqel Premium Vape - Starting Flask App
echo ============================================
echo [1/2] Installing dependencies...
pip install -r requirements.txt >nul 2>&1
echo [2/2] Starting server...
echo.
echo Toko   : http://127.0.0.1:5000
echo Admin  : http://127.0.0.1:5000/admin
echo.
echo ---- OPSIONAL: Email Gmail otomatis ----
echo   set GMAIL_USER=email_kamu@gmail.com
echo   set GMAIL_PASS=app_password_16_karakter
echo ----------------------------------------
echo.
python app.py
pause
