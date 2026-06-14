@echo off
cd /d "%~dp0backend"
if "%OLLAMA_MODEL%"=="" set "OLLAMA_MODEL=gemma4:latest"
if "%OLLAMA_VISION_MODEL%"=="" set "OLLAMA_VISION_MODEL=gemma4:latest"
if "%OLLAMA_VISION_ENABLED%"=="" set "OLLAMA_VISION_ENABLED=true"
if "%OLLAMA_BASE_URL%"=="" set "OLLAMA_BASE_URL=http://localhost:11434"
set "UV=%USERPROFILE%\.local\bin\uv.exe"
if exist "%UV%" (
  "%UV%" run python main.py
) else (
  uv run python main.py
)
pause
