"""Windows 全局快捷键（RegisterHotKey）与快捷键解析。"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

RECORD_HOTKEY_ID = 0xA2F1
SHOW_HOTKEY_ID = 0xA2F2
_TEST_HOTKEY_ID = 0x5EED

VK_F9 = 0x78
VK_LWIN = 0x5B
VK_RWIN = 0x5C

_MOD_ALIASES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
    "super": MOD_WIN,
    "meta": MOD_WIN,
}

# 虚拟键码 -> 显示名称。解析与按键录制共用这张表。
_KEY_NAMES: dict[int, str] = {}
for _ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _KEY_NAMES[ord(_ch)] = _ch
for _ch in "0123456789":
    _KEY_NAMES[ord(_ch)] = _ch
for _n in range(1, 25):
    _KEY_NAMES[0x6F + _n] = f"F{_n}"
for _n in range(10):
    _KEY_NAMES[0x60 + _n] = f"Num{_n}"
_KEY_NAMES.update(
    {
        0x20: "Space",
        0x0D: "Enter",
        0x1B: "Esc",
        0x09: "Tab",
        0x08: "Backspace",
        0x2E: "Delete",
        0x2D: "Insert",
        0x24: "Home",
        0x23: "End",
        0x21: "PageUp",
        0x22: "PageDown",
        0x25: "Left",
        0x26: "Up",
        0x27: "Right",
        0x28: "Down",
        0x13: "Pause",
        0x2C: "PrintScreen",
        0x6A: "NumMultiply",
        0x6B: "NumAdd",
        0x6D: "NumSubtract",
        0x6E: "NumDecimal",
        0x6F: "NumDivide",
        0xBB: "Plus",
        0xBC: "Comma",
        0xBD: "Minus",
        0xBE: "Period",
        0xBA: "Semicolon",
        0xBF: "Slash",
        0xC0: "Backquote",
        0xDB: "LeftBracket",
        0xDC: "Backslash",
        0xDD: "RightBracket",
        0xDE: "Quote",
    }
)

_KEY_ALIASES = {
    "return": 0x0D,
    "escape": 0x1B,
    "del": 0x2E,
    "ins": 0x2D,
    "pgup": 0x21,
    "pgdn": 0x22,
    "prtsc": 0x2C,
    "printscreen": 0x2C,
    "spacebar": 0x20,
    "equal": 0xBB,
    "plus": 0xBB,
    "dot": 0xBE,
    "add": 0x6B,
    "subtract": 0x6D,
    "decimal": 0x6E,
    "divide": 0x6F,
    "multiply": 0x6A,
}

_KEY_TABLE: dict[str, tuple[int, str]] = {}
for _vk, _label in _KEY_NAMES.items():
    _KEY_TABLE[_label.lower()] = (_vk, _label)
for _name, _vk in _KEY_ALIASES.items():
    _KEY_TABLE.setdefault(_name, (_vk, _KEY_NAMES[_vk]))


@dataclass(frozen=True)
class HotkeySpec:
    """一个已解析的全局快捷键：修饰键 + 虚拟键码。"""

    mods: int
    vk: int
    key: str

    @property
    def text(self) -> str:
        parts: list[str] = []
        if self.mods & MOD_CONTROL:
            parts.append("Ctrl")
        if self.mods & MOD_ALT:
            parts.append("Alt")
        if self.mods & MOD_SHIFT:
            parts.append("Shift")
        if self.mods & MOD_WIN:
            parts.append("Win")
        parts.append(self.key)
        return "+".join(parts)


RECORD_HOTKEY_SPEC = HotkeySpec(0, VK_F9, "F9")


def hotkey_name_for_vk(vk: int) -> str | None:
    """录制按键时把虚拟键码转成显示名称；不支持的键返回 None。"""
    return _KEY_NAMES.get(int(vk))


def parse_hotkey(text: str) -> HotkeySpec:
    """解析 Ctrl+Alt+R 这类文本，失败时抛出 ValueError。"""
    raw = text.strip()
    if not raw:
        raise ValueError("快捷键不能为空")
    parts = [part.strip() for part in raw.split("+")]
    if len(parts) < 2 or any(not part for part in parts):
        raise ValueError("格式不正确，示例：Ctrl+Alt+R")
    *mod_parts, key_part = parts
    mods = 0
    for part in mod_parts:
        bit = _MOD_ALIASES.get(part.lower())
        if bit is None:
            raise ValueError(f"未知的修饰键：{part}")
        mods |= bit
    if key_part.lower() in _MOD_ALIASES:
        raise ValueError("还需要一个普通按键，例如 R、F9 或 Space")
    entry = _KEY_TABLE.get(key_part.lower())
    if entry is None:
        raise ValueError(f"不支持的按键：{key_part}")
    if not mods:
        raise ValueError("至少需要一个修饰键（Ctrl、Alt、Shift 或 Win）")
    vk, label = entry
    return HotkeySpec(mods, vk, label)


def modifiers_from_state(state: int) -> int:
    """把 Tk 的 event.state 转成 RegisterHotKey 的修饰键位。"""
    mods = 0
    if state & 0x0001:
        mods |= MOD_SHIFT
    if state & 0x0004:
        mods |= MOD_CONTROL
    if sys.platform == "win32":
        if state & 0x20000:
            mods |= MOD_ALT
    elif state & 0x0008:
        mods |= MOD_ALT
    return mods


def is_win_key_pressed() -> bool:
    """Win 键不会出现在 Tk 的 event.state 中，单独查询系统按键状态。"""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        return bool(
            user32.GetAsyncKeyState(VK_LWIN) & 0x8000
            or user32.GetAsyncKeyState(VK_RWIN) & 0x8000
        )
    except Exception:
        return False


def hotkey_available(spec: HotkeySpec) -> bool:
    """临时注册再注销，用于在保存前检测快捷键是否被占用。"""
    if sys.platform != "win32":
        return False
    user32 = ctypes.windll.user32
    try:
        registered = bool(
            user32.RegisterHotKey(
                None, _TEST_HOTKEY_ID, spec.mods | MOD_NOREPEAT, spec.vk
            )
        )
    except Exception:
        logger.debug("测试快捷键注册失败", exc_info=True)
        return False
    if registered:
        try:
            user32.UnregisterHotKey(None, _TEST_HOTKEY_ID)
        except Exception:
            logger.debug("注销测试快捷键失败", exc_info=True)
    return registered


class GlobalHotkey:
    """在独立线程中注册系统级热键，回调运行在该线程。"""

    def __init__(
        self,
        callback: Callable[[], None],
        spec: HotkeySpec = RECORD_HOTKEY_SPEC,
        hotkey_id: int = RECORD_HOTKEY_ID,
        name: str = "全局快捷键",
    ):
        self._callback = callback
        self.spec = spec
        self._hotkey_id = hotkey_id
        self._name = name
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._active = threading.Event()
        self._started = False

    @property
    def active(self) -> bool:
        return self._active.is_set()

    def start(self) -> bool:
        if sys.platform != "win32" or self._started:
            return self.active
        self._started = True
        self._thread = threading.Thread(
            target=self._run,
            name="r2-hotkey",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(1.0)
        return self.active

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        thread_id = self._thread_id
        if thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(
                    thread_id, WM_QUIT, 0, 0
                )
            except Exception:
                logger.debug("停止全局快捷键失败", exc_info=True)
        self._thread = None
        self._active.clear()

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        registered = False
        try:
            registered = bool(
                user32.RegisterHotKey(
                    None,
                    self._hotkey_id,
                    self.spec.mods | MOD_NOREPEAT,
                    self.spec.vk,
                )
            )
        except Exception:
            logger.exception("注册全局快捷键失败")
        if not registered:
            logger.warning(
                "%s %s 注册失败（可能已被其它程序占用）",
                self._name,
                self.spec.text,
            )
            self._thread_id = None
            self._started = False
            self._ready.set()
            return

        self._active.set()
        self._ready.set()
        logger.info("%s %s 已注册", self._name, self.spec.text)

        try:
            message = wintypes.MSG()
            while True:
                result = user32.GetMessageW(
                    ctypes.byref(message), None, 0, 0
                )
                if result in (0, -1):
                    break
                if (
                    message.message == WM_HOTKEY
                    and int(message.wParam) == self._hotkey_id
                ):
                    try:
                        self._callback()
                    except Exception:
                        logger.exception("全局快捷键回调失败")
        finally:
            try:
                user32.UnregisterHotKey(None, self._hotkey_id)
            except Exception:
                pass
            self._active.clear()
            self._thread_id = None
