@echo off
py -3 "%~dp0scripts\launch_workbench.py" %*
if errorlevel 1 pause
