' Starts the Advisor Lens server with NO console window (window style 0 = hidden).
' Called by launch_advisor_lens.bat, which sets VENV, PORT and LOG in the environment.
Set sh = CreateObject("WScript.Shell")
venv = sh.ExpandEnvironmentStrings("%VENV%")
port = sh.ExpandEnvironmentStrings("%PORT%")
logf = sh.ExpandEnvironmentStrings("%LOG%")
sh.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
cmd = "cmd /c """"" & venv & "\Scripts\python.exe"" -m streamlit run app.py --server.port " & port & _
      " --server.headless true > """ & logf & """ 2>&1"""
sh.Run cmd, 0, False
