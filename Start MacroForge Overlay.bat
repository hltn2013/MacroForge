@echo off
rem Starts the MacroForge overlay in the background.
rem Keep this file in the same folder as MacroForge-Overlay.ps1, then double-click it.
start "" powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0MacroForge-Overlay.ps1"
