@echo off
REM Build test_scale.exe on a Windows PC (PyInstaller cannot cross-compile from macOS).
REM Requires Python 3.8+ from python.org ("py" launcher).
cd /d "%~dp0"

if not exist .venv (
    py -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error
pip install -r requirements-build.txt || goto :error

pyinstaller --onefile --console --clean --name test_scale test_scale.py || goto :error

echo.
echo Done: dist\test_scale.exe
echo Copy that single file to the test PC and run it from CMD, e.g.:
echo     test_scale.exe --port COM3
goto :eof

:error
echo.
echo BUILD FAILED
exit /b 1
