Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "f:\pc software\dist_v3\WebcamMaster"
WshShell.Run """f:\pc software\dist_v3\WebcamMaster\WebcamMaster.exe""", 1, False
