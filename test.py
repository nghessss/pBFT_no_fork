import subprocess

subprocess.Popen(
    ["cmd.exe", "/k", "echo Hello from Windows CMD"],
    creationflags=subprocess.CREATE_NEW_CONSOLE
)
