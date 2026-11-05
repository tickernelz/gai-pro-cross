@echo off
setlocal
set "HERE=%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%\launchers\run-windows.ps1" %*
