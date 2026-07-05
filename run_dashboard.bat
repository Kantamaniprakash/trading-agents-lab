@echo off
rem ------------------------------------------------------------------
rem  trading-agents-lab -- trader dashboard launcher (no coding needed)
rem  Installs the dashboard dependencies, opens your browser, and
rem  starts the Streamlit app. Close this window to stop the dashboard.
rem ------------------------------------------------------------------
cd /d %~dp0
echo Installing dashboard dependencies (first run may take a minute)...
python -m pip install -q -r requirements-app.txt
if errorlevel 1 (
    echo.
    echo ERROR: could not install dependencies. Is Python installed and on PATH?
    pause
    exit /b 1
)
echo Opening http://localhost:8501 ...
start http://localhost:8501
python -m streamlit run app.py
pause
