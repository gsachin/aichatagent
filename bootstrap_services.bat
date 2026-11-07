@echo off
rem One-click bootstrap for the University Admissions Voice Assistant.
rem Checks pre-requisites, installs missing software, starts all services.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap_services.ps1" %*
if errorlevel 1 pause
