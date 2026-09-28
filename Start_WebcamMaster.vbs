Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "f:\pc software"
WshShell.Run """f:\pc software\WebcamMaster.exe""", 1, False
