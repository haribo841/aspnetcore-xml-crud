Option Explicit
Dim shell, fso, folder, python, script
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
python = folder & "\.venv\Scripts\pythonw.exe"
script = folder & "\kolejka.py"
If Not fso.FileExists(python) Then
    MsgBox "Najpierw uruchom Instaluj-zaleznosci.ps1.", 48, "Kolejka transkrypcji"
Else
    shell.CurrentDirectory = folder
    shell.Run """" & python & """ """ & script & """ gui --local", 1, False
End If
