@echo off
chcp 65001 >nul
rem usage: run_mesh_sets.bat            (models 폴더 전부)
rem        run_mesh_sets.bat Part2 Part5 (이름으로 골라서)
cd /d %~dp0
python yl_mesh_sets.py config_mesh_sets.json %*
echo.
echo ===== finished. check out\mesh_sets_log.txt =====
pause
