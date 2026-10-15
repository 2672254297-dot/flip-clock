' ---------------------------------------------------------------
'  Flip Clock - MANUAL MODE, silently (no console window)
'  Fullscreen clock right away; Esc or Alt+F4 to exit.
' ---------------------------------------------------------------
Option Explicit
Dim fso, sh, baseDir
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = baseDir
sh.Run "pythonw """ & baseDir & "\app\flipclock_overlay.py"" --manual", 0, False
