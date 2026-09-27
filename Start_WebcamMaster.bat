@echo off
title Webcam Master — Wireless Phone Mic, Camera & PC Screen Recorder
cd /d "%~dp0"
if exist "WebcamMaster.exe" (
    if exist "_internal" (
        start "" "WebcamMaster.exe"
        exit /b 0
    )
)
python "pc_app\motomic_pc.py"
