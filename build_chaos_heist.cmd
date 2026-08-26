@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_chaos_heist.ps1" %*
