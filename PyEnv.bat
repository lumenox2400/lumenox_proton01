@echo off
@REM cd /d %~dp0

echo Creating virtual environment
python -m venv .venv

echo Activating virtual environment
call .\.venv\Scripts\activate || exit /b 1

echo Upgrading pip
python -m pip install --upgrade pip

echo Installing dependencies from setup.cfg or setup.py
pip install -e .

echo Installing Chromium for Playwright
playwright install --with-deps chromium

echo Dependencies installed successfully.

pause