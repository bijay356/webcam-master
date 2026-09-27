@echo off
title MotoMic & Cam - Filmora Virtual Mic & Webcam Driver Installer
echo =====================================================================
echo   Installing Virtual Webcam (Unity Video Capture) for Filmora...
echo =====================================================================
regsvr32 /s "f:\pc software\drivers\unitycapture\UnityCapture-master\Install\UnityCaptureFilter32.dll"
regsvr32 /s "f:\pc software\drivers\unitycapture\UnityCapture-master\Install\UnityCaptureFilter64.dll"
echo [OK] Virtual Webcam "Unity Video Capture" registered!

echo.
echo =====================================================================
echo   Installing Virtual Microphone (VB-Audio Virtual Cable) for Filmora...
echo =====================================================================
pushd "f:\pc software\drivers\vbcable"
"f:\pc software\drivers\vbcable\VBCABLE_Setup_x64.exe" -i -h
popd
echo [OK] Virtual Microphone "CABLE Output (VB-Audio Virtual Cable)" installed!
echo.
echo Done! You can now select:
echo   - Microphone: CABLE Output (VB-Audio Virtual Cable)
echo   - Camera:     Unity Video Capture
echo inside Wondershare Filmora or any PC software!
timeout /t 4
