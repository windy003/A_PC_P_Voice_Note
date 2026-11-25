"""应用内使用的目录与文件路径。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _app_dir() -> Path:
    # 打包成 exe 后使用 exe 所在目录，源码运行时使用项目根目录。
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_DIR = _app_dir()
ENV_PATH = APP_DIR / ".env"
ENV_EXAMPLE_PATH = APP_DIR / ".env.example"


def _data_dir() -> Path:
    override = os.environ.get("OSS_RECORDER_DATA_DIR", "").strip()
    if not override:
        # 兼容旧版本使用的环境变量名。
        override = os.environ.get("R2_RECORDER_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return APP_DIR / "data"


DATA_DIR = _data_dir()
RECORDINGS_DIR = DATA_DIR / "recordings"
META_PATH = DATA_DIR / "recordings.json"
CACHE_DIR = DATA_DIR / "cache"
LOG_PATH = DATA_DIR / "app.log"


def ensure_dirs() -> None:
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
