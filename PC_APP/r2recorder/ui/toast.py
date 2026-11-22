"""窗口内 toast 与屏幕级全局提示。"""

from __future__ import annotations

import ctypes
import logging
import sys
import tkinter as tk
import tkinter.font as tkfont

from .theme import Theme, UI_FONT
from .widgets import draw_round_rect, parent_bg

logger = logging.getLogger(__name__)

TRANSPARENT_COLOR = "#010203"


class Toast(tk.Canvas):
    """浮在窗口底部的圆角提示条，自动消失。"""

    HEIGHT = 44

    def __init__(self, master: tk.Misc, theme: Theme):
        super().__init__(
            master,
            width=200,
            height=self.HEIGHT,
            bg=parent_bg(master),
            highlightthickness=0,
            bd=0,
        )
        self._theme = theme
        self._font = tkfont.Font(family=UI_FONT, size=9, weight="bold")
        self._hide_id: str | None = None

    def show(
        self,
        text: str,
        *,
        error: bool = False,
        duration_ms: int = 2600,
    ) -> None:
        self._cancel()
        max_width = 560
        width = max(150, min(max_width, self._font.measure(text) + 48))
        fill = self._theme.error if error else self._theme.on_surface
        text_color = self._theme.chip_text if error else self._theme.surface

        self.configure(width=width, height=self.HEIGHT)
        self.delete("all")
        draw_round_rect(
            self,
            0,
            0,
            width - 1,
            self.HEIGHT - 1,
            self.HEIGHT / 2,
            fill,
        )
        self.create_text(
            width / 2,
            self.HEIGHT / 2,
            text=text,
            fill=text_color,
            font=self._font,
        )
        self.place(relx=0.5, rely=1.0, y=-56, anchor="s")
        tk.Misc.lift(self)
        self._hide_id = self.after(duration_ms, self.hide)

    def hide(self) -> None:
        self._cancel()
        try:
            self.place_forget()
        except tk.TclError:
            pass

    def _cancel(self) -> None:
        if self._hide_id is not None:
            try:
                self.after_cancel(self._hide_id)
            except tk.TclError:
                pass
            self._hide_id = None


def _work_area(root: tk.Misc) -> tuple[int, int, int, int]:
    """返回鼠标所在显示器的可用区域（已排除任务栏）。"""
    if sys.platform == "win32":
        try:
            from ctypes import wintypes

            user32 = ctypes.windll.user32

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD),
                ]

            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            monitor = user32.MonitorFromPoint(point, 2)  # NEAREST
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                rect = info.rcWork
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            logger.debug("获取显示器可用区域失败", exc_info=True)
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight() - 60


class GlobalToast:
    """屏幕下方居中的置顶提示窗口，不受主窗口状态及其他程序影响。"""

    HEIGHT = 48
    PADDING = 22
    MAX_WIDTH = 640

    def __init__(self, root: tk.Misc, theme: Theme):
        self._root = root
        self._theme = theme
        self._window: tk.Toplevel | None = None
        self._hide_id: str | None = None
        self._font = tkfont.Font(
            root=root, family=UI_FONT, size=10, weight="bold"
        )

    def show(
        self,
        text: str,
        *,
        error: bool = False,
        duration_ms: int = 3000,
    ) -> None:
        self.hide()
        theme = self._theme

        if self._font.measure(text) > self.MAX_WIDTH - self.PADDING * 2:
            while text and (
                self._font.measure(text + "…")
                > self.MAX_WIDTH - self.PADDING * 2
            ):
                text = text[:-1]
            text += "…"

        width = max(
            200,
            min(
                self.MAX_WIDTH,
                self._font.measure(text) + self.PADDING * 2,
            ),
        )
        height = self.HEIGHT

        window = tk.Toplevel(self._root)
        window.withdraw()
        window.overrideredirect(True)
        try:
            window.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        background = theme.surface
        if sys.platform == "win32":
            try:
                window.wm_attributes("-transparentcolor", TRANSPARENT_COLOR)
                background = TRANSPARENT_COLOR
            except tk.TclError:
                pass
        window.configure(bg=background)

        canvas = tk.Canvas(
            window,
            width=width,
            height=height,
            bg=background,
            highlightthickness=0,
            bd=0,
        )
        canvas.pack()
        fill = theme.error if error else theme.on_surface
        text_color = theme.chip_text if error else theme.surface
        draw_round_rect(
            canvas,
            0,
            0,
            width - 1,
            height - 1,
            height / 2,
            fill,
        )
        canvas.create_text(
            width / 2,
            height / 2,
            text=text,
            fill=text_color,
            font=self._font,
        )

        left, top, right, bottom = _work_area(self._root)
        margin = 32
        x = left + max(0, ((right - left) - width) // 2)
        y = max(top, bottom - height - margin)
        window.geometry(f"{width}x{height}+{x}+{y}")

        self._window = window
        window.deiconify()
        try:
            tk.Misc.lift(window)
        except tk.TclError:
            pass
        self._hide_id = self._root.after(duration_ms, self.hide)

    def hide(self) -> None:
        self._cancel()
        window, self._window = self._window, None
        if window is not None:
            try:
                window.destroy()
            except tk.TclError:
                pass

    def _cancel(self) -> None:
        if self._hide_id is not None:
            try:
                self._root.after_cancel(self._hide_id)
            except tk.TclError:
                pass
            self._hide_id = None

