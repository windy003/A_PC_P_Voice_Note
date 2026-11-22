"""Windows 全局快捷键（RegisterHotKey）。"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from typing import Callable

logger = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000
VK_F9 = 0x78
HOTKEY_ID = 0xA2F1


class GlobalHotkey:
    """在独立线程中注册系统级热键，回调运行在该线程。"""

    def __init__(self, callback: Callable[[], None], vk: int = VK_F9):
        self._callback = callback
        self._vk = vk
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
                    None, HOTKEY_ID, MOD_NOREPEAT, self._vk
                )
            )
        except Exception:
            logger.exception("注册全局快捷键失败")
        if registered:
            self._active.set()
            logger.info("全局快捷键 F9 已注册")
        else:
            logger.warning(
                "全局快捷键 F9 注册失败（可能已被其它程序占用）"
            )
        self._ready.set()
        if not registered:
            return

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
                    and int(message.wParam) == HOTKEY_ID
                ):
                    try:
                        self._callback()
                    except Exception:
                        logger.exception("全局快捷键回调失败")
        finally:
            try:
                user32.UnregisterHotKey(None, HOTKEY_ID)
            except Exception:
                pass
            self._active.clear()
            self._thread_id = None
