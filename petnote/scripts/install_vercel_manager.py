"""Create the current user's PetNote desktop shortcut without saving credentials."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def ps_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def main():
    if os.name != "nt":
        raise SystemExit("바탕화면 설치는 Windows에서 실행해 주세요.")
    # This stdlib-only tool can use base pythonw directly. Some Windows venv
    # redirectors start python.exe and create an unwanted terminal window.
    pythonw = Path(sys.base_prefix) / "pythonw.exe"
    legacy_pythonw = Path(sys.executable).with_name("pythonw.exe")
    script = ROOT / "scripts" / "vercel_manager.py"
    if not pythonw.is_file() or not script.is_file():
        raise SystemExit("프로젝트의 Python 환경과 서버 관리 파일을 찾지 못했습니다.")
    arguments = subprocess.list2cmdline(["-X", "utf8", str(script)])
    command = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
        "$ErrorActionPreference = 'Stop'; "
        "$desktop = [Environment]::GetFolderPath('Desktop'); "
        "if (-not (Test-Path -LiteralPath $desktop -PathType Container)) { throw 'Desktop not found' }; "
        "$path = Join-Path $desktop '펫노트 서버 관리.lnk'; "
        "$shell = New-Object -ComObject WScript.Shell; "
        "$link = $shell.CreateShortcut($path); "
        f"if ((Test-Path -LiteralPath $path) -and (($link.TargetPath -notin @({ps_string(pythonw)}, {ps_string(legacy_pythonw)})) "
        f"-or ($link.Arguments -ne {ps_string(arguments)}))) "
        "{ throw 'An unrelated shortcut with this name already exists' }; "
        f"$link.TargetPath = {ps_string(pythonw)}; "
        f"$link.Arguments = {ps_string(arguments)}; "
        f"$link.WorkingDirectory = {ps_string(ROOT)}; "
        f"$link.IconLocation = {ps_string(str(pythonw) + ',0')}; "
        "$link.Description = '펫노트 공개 서버 켜기, 끄기, 상태 확인'; "
        "$link.WindowStyle = 1; $link.Save(); "
        "[pscustomobject]@{shortcut=$path; target=$link.TargetPath; arguments=$link.Arguments; workingDirectory=$link.WorkingDirectory} | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise SystemExit("바로가기를 만들지 못했습니다. 바탕화면 접근 권한과 같은 이름의 바로가기를 확인해 주세요.")
    info = json.loads(result.stdout.decode("utf-8-sig"))
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
