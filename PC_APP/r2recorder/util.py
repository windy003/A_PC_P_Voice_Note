"""系统相关的小工具。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_path(path: str | os.PathLike[str]) -> None:
    """用系统默认程序打开文件或文件夹。"""
    target = str(Path(path))
    if sys.platform == "win32":
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])
