@echo off
chcp 65001 >nul
REM usage:  run_all.bat smoke   (Abaqus only, 4 jobs, no HyperMesh)
REM         run_all.bat         (full: HyperMesh -> INP -> Abaqus -> ODB -> report)
cd /d %~dp0
if not exist auto_out mkdir auto_out
python check_env.py config_abaqus.json > auto_out\check_env.txt 2>&1
type auto_out\check_env.txt
if "%1"=="smoke" (
  python run_pipeline.py config_abaqus_smoketest.json
  python collect_logs.py config_abaqus_smoketest.json
  goto :end
)
python run_pipeline.py config_abaqus.json %*
python collect_logs.py config_abaqus.json
:end
echo.
echo ===== finished. check auto_out\pipeline_log.txt =====
pause
