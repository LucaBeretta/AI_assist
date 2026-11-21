"""Create native, windowless shortcuts to this installation."""
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys

APP = Path(__file__).resolve().parent


def windows_spec(root, executable):
    private = root / "runtime" / "python" / "pythonw.exe"
    pythonw = private if private.is_file() else Path(executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise ValueError("The shortcut requires pythonw.exe beside the working Python.")
    icon = root / "assets/icon/ai-assist.ico"
    return {"TargetPath": str(pythonw), "WorkingDirectory": str(root),
            "Arguments": f'"{root / "gui_runtime.py"}" --launch "{root / "AIassist.py"}"',
            "IconLocation": str(icon) if icon.is_file() else ""}


WINDOWS_SCRIPT = """
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    if (-not $dir) { throw 'The shortcut folder is unavailable.' }
    $null = [IO.Directory]::CreateDirectory($dir)
    $link = $shell.CreateShortcut((Join-Path $dir 'AI Assist.lnk'))
    $link.TargetPath = $env:SHORTCUT_TargetPath
    $link.Arguments = $env:SHORTCUT_Arguments
    $link.WorkingDirectory = $env:SHORTCUT_WorkingDirectory
    if ($env:SHORTCUT_IconLocation) { $link.IconLocation = $env:SHORTCUT_IconLocation }
    $link.Save()
}
"""


def mac_bundle(root, python, icon):
    info = {"CFBundleName": "AI Assist", "CFBundleIdentifier": "com.aiassist.launcher",
            "CFBundleExecutable": "AI Assist", "CFBundlePackageType": "APPL"}
    if icon:
        info["CFBundleIconFile"] = "ai-assist.icns"
    script = ("#!/bin/sh\ncd " + shlex.quote(str(root)) + " || exit 1\nexec " +
              shlex.quote(str(python)) + " gui_runtime.py --launch AIassist.py\n")
    return plistlib.dumps(info), script


def desktop_entry(root, python, icon):
    # Exec is parsed twice: desktop-string escapes, then command argument quoting.
    def arg(path):
        value = str(path).replace("%", "%%")
        for char in ('\\', '"', '$', '\x60'):
            value = value.replace(char, '\\' + char)
        return '"' + value.replace('\\', '\\\\') + '"'
    def field(path):
        return str(path).replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")
    if any(c in str(root) + str(python) for c in "\r\n"):
        raise ValueError("The desktop launcher requires a single-line path.")
    cmd = f'{arg(python)} {arg(root / "gui_runtime.py")} --launch {arg(root / "AIassist.py")}'
    return ("[Desktop Entry]\nType=Application\nName=AI Assist\nTerminal=false\n"
            f"Exec={cmd}\nPath={field(root)}\n" + (f"Icon={field(icon)}\n" if icon else ""))


def create_shortcut(root=APP, executable=None):
    root = Path(root).resolve()
    executable = Path(executable or sys.executable)
    if sys.platform == "win32":
        spec = windows_spec(root, executable)
        env = {**os.environ, **{"SHORTCUT_" + k: v for k, v in spec.items()}}
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", WINDOWS_SCRIPT],
                       env=env, check=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
        return
    home = Path.home()
    desktop = home / "Desktop"
    if sys.platform == "darwin":
        private = root / "runtime/python/bin/python3"
        bundle = home / "Applications/AI Assist.app"
        contents = bundle / "Contents"
        icon = root / "assets/icon/ai-assist.icns"
        info, script = mac_bundle(root, private if private.is_file() else executable, icon.is_file())
        entry = contents / "MacOS/AI Assist"
        entry.parent.mkdir(parents=True, exist_ok=True)
        (contents / "Info.plist").write_bytes(info)
        entry.write_bytes(script.encode("utf-8"))
        entry.chmod(0o755)
        if icon.is_file():
            (contents / "Resources").mkdir(exist_ok=True)
            shutil.copyfile(icon, contents / "Resources" / icon.name)
        desktop.mkdir(exist_ok=True)
        link = desktop / bundle.name
        if link.is_symlink():
            link.unlink()
        link.symlink_to(bundle, target_is_directory=True)
    else:
        private = root / "runtime/linux/python/bin/python3"
        icon = root / "assets/icon/ai-assist.png"
        entry = home / ".local/share/applications/ai-assist.desktop"
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_bytes(desktop_entry(root, private if private.is_file() else executable,
                                        icon if icon.is_file() else None).encode("utf-8"))
        entry.chmod(0o755)
        if desktop.is_dir():
            shutil.copy2(entry, desktop / entry.name)


def ask_once(root=APP, ask=input, create=create_shortcut):
    marker = root / "runtime/.shortcut-asked"
    if marker.exists():
        return
    try:
        answer = ask("Create a shortcut on the desktop? (Y/n) ").strip().lower()
    except EOFError:
        return
    if answer in {"", "y", "yes"}:
        create(root)
    marker.parent.mkdir(exist_ok=True)
    marker.write_text(("yes" if answer in {"", "y", "yes"} else "no") + "\n", encoding="utf-8")
