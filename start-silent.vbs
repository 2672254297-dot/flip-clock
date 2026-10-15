' ---------------------------------------------------------------
'  Flip Clock Overlay - start host silently (no console window)
' ---------------------------------------------------------------
Option Explicit
Dim fso, sh, baseDir
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = baseDir
sh.Run "pythonw """ & baseDir & "\app\flipclock_overlay.py""", 0, False
