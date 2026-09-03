@echo off
REM Construit tubeiso.exe. A lancer une seule fois, sur une machine Windows
REM disposant de Python 3.11 ou plus recent.
setlocal
echo.
echo   Construction de tubeiso.exe
echo   ---------------------------
echo.
python --version >nul 2>&1 || (echo Python introuvable. Installez-le depuis python.org ^(cochez "Add to PATH"^). & pause & exit /b 1)
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt pyinstaller -q || (echo Echec de l'installation des dependances. & pause & exit /b 1)
python tests\test_tubeiso.py || (echo Les tests echouent, construction interrompue. & pause & exit /b 1)
pyinstaller tubeiso.spec --noconfirm --clean || (echo Echec de PyInstaller. & pause & exit /b 1)
echo.
echo   Termine. L'executable est dans  dist\tubeiso\tubeiso.exe
echo   Distribuez le dossier dist\tubeiso complet, pas seulement le .exe.
echo.
pause
