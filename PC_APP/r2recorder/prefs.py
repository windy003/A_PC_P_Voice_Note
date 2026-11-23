"""本机偏好设置：保存在 data/settings.json。"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .paths import DATA_DIR

logger = logging.getLogger(__name__)

SETTINGS_PATH = DATA_DIR / "settings.json"
DEFAULT_SHOW_HOTKEY = "Ctrl+Alt+R"


@dataclass
class Preferences:
    show_hotkey: str = DEFAULT_SHOW_HOTKEY


def load_preferences(path: str | os.PathLike[str] = SETTINGS_PATH) -> Preferences:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return Preferences()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("设置文件格式不正确，已使用默认设置")
        return Preferences()
    if not isinstance(data, dict):
        return Preferences()
    value = data.get("show_hotkey", DEFAULT_SHOW_HOTKEY)
    if not isinstance(value, str):
        value = DEFAULT_SHOW_HOTKEY
    return Preferences(show_hotkey=value.strip())


def save_preferences(
    preferences: Preferences,
    path: str | os.PathLike[str] = SETTINGS_PATH,
) -> None:
    target = Path(path)
    data = {"show_hotkey": preferences.show_hotkey.strip()}
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".settings.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, target)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
