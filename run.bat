@echo off
chcp 65001 >nul
title Trend Radar
cd /d "%~dp0"

where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul || (
  echo Python не найден. Установи Python 3.12 с https://www.python.org/downloads/ ^(галочка "Add python.exe to PATH"^)
  echo или командой: winget install Python.Python.3.12
  pause & exit /b 1
)

if not exist .venv (
  echo Создаю окружение...
  %PY% -m venv .venv || (pause & exit /b 1)
)
call .venv\Scripts\activate.bat
if not exist .venv\installed.flag (
  echo Ставлю зависимости, это 1-2 минуты...
  python -m pip install --upgrade pip -q
  python -m pip install -r requirements.txt -q || (pause & exit /b 1)
  echo ok> .venv\installed.flag
)

if not exist .env (
  copy .env.example .env >nul
  echo.
  echo Открываю настройки .env: впиши BOT_TOKEN, PARTNER_API_KEY и ADMIN_TOKEN, сохрани и закрой Блокнот.
  notepad .env
)

echo Запускаю Trend Radar. Остановить: Ctrl+C или закрыть окно.
python -m app.main
pause
