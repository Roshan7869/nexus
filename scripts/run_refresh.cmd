@echo off
REM NEXUS weekly refresh (W5). Invoked by the Windows scheduled task.
REM Keeps the catalogue, embeddings, graph and Obsidian vault in step with the
REM source repos. Safe to run manually:  scripts\run_refresh.cmd
REM
REM Exit codes: 0 ok, non-zero = a stage failed (see the log).
setlocal

set "REPO=%~dp0.."
set "PY=D:\dev\nexus-venv\Scripts\python.exe"
set "NEXUS_DB_PATH=D:\nexus-data\db\nexus.db"
set "FASTEMBED_CACHE_PATH=D:\nexus-data\models\fastembed"
set "USE_UNIFIED_ROUTER=2"
set "NEXUS_CLUSTER_ROUTING=true"
set "PYTHONIOENCODING=utf-8"

set "LOGDIR=D:\nexus-data\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set "LOG=%LOGDIR%\refresh.log"

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo refresh start %DATE% %TIME% >> "%LOG%"
echo ============================================================ >> "%LOG%"

cd /d "%REPO%"
"%PY%" scripts\refresh_nexus.py >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"

echo refresh end %DATE% %TIME% ^(rc=%RC%^) >> "%LOG%"
exit /b %RC%
