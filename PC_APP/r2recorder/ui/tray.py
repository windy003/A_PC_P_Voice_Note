"""系统托盘图标与右键菜单。"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover - 依赖缺失时禁用托盘
    pystray = None
    Image = None
    ImageDraw = None

TITLE = "R2 录音机"


def icon_image(size: int = 64):
    """绘制一个红底白色麦克风的图标（托盘与窗口共用）。"""
    if Image is None or ImageDraw is None:
        return None
    scale = 4
    edge = size * scale
    image = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, edge - 1, edge - 1), fill=(198, 40, 40, 255))
    width = max(2, int(edge * 0.05))
    draw.rounded_rectangle(
        (edge * 0.38, edge * 0.14, edge * 0.62, edge * 0.56),
        radius=int(edge * 0.12),
        fill="white",
    )
    draw.arc(
        (edge * 0.30, edge * 0.30, edge * 0.70, edge * 0.64),
        start=0,
        end=180,
        fill="white",
        width=width,
    )
    draw.line(
        (edge * 0.50, edge * 0.62, edge * 0.50, edge * 0.78),
        fill="white",
        width=width,
    )
    draw.line(
        (edge * 0.38, edge * 0.80, edge * 0.62, edge * 0.80),
        fill="white",
        width=width,
    )
    return image.resize((size, size), Image.LANCZOS)


class Tray:
    """托盘图标；菜单回调通过 post 切回 Tk 主线程执行。"""

    def __init__(
        self,
        post: Callable[[Callable[[], None]], None],
        *,
        on_show: Callable[[], None],
        on_toggle_record: Callable[[], None],
        on_quit: Callable[[], None],
        on_restart: Callable[[], None],
    ):
        self._post = post
        self._on_show = on_show
        self._on_toggle_record = on_toggle_record
        self._on_quit = on_quit
        self._on_restart = on_restart
        self._icon = None
        self._started = False
        self._recording = False

    @property
    def available(self) -> bool:
        return pystray is not None

    def start(self) -> bool:
        if not self.available or self._started:
            return self._started
        try:
            icon = pystray.Icon("r2recorder", icon_image(), TITLE, self._menu())
        except Exception:
            logger.exception("创建系统托盘图标失败")
            return False
        self._icon = icon
        self._started = True
        threading.Thread(
            target=self._run,
            args=(icon,),
            name="r2-tray",
            daemon=True,
        ).start()
        return True

    def _run(self, icon) -> None:
        try:
            icon.run()
        except Exception:
            logger.exception("系统托盘图标运行失败")

    def _menu(self):
        return pystray.Menu(
            pystray.MenuItem("显示主窗口", self._menu_show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._record_text, self._menu_toggle_record),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("重启(&R)", self._menu_restart),
            pystray.MenuItem("关闭(&X)", self._menu_quit),
        )

    def _record_text(self, _item) -> str:
        return "停止录音" if self._recording else "开始录音"

    def _menu_show(self, _icon, _item) -> None:
        self._post(self._on_show)

    def _menu_toggle_record(self, _icon, _item) -> None:
        self._post(self._on_toggle_record)

    def _menu_restart(self, _icon, _item) -> None:
        self._post(self._on_restart)

    def _menu_quit(self, _icon, _item) -> None:
        self._post(self._on_quit)

    def set_recording(self, recording: bool) -> None:
        recording = bool(recording)
        if recording == self._recording:
            return
        self._recording = recording
        icon = self._icon
        if icon is None:
            return
        try:
            icon.title = f"{TITLE} · 正在录音" if recording else TITLE
            icon.update_menu()
        except Exception:
            logger.debug("更新托盘状态失败", exc_info=True)

    def notify(self, message: str, *, error: bool = False) -> bool:
        """发送系统级通知；不可用时返回 False。"""
        icon = self._icon
        if icon is None:
            return False
        try:
            icon.notify(str(message), TITLE)
            return True
        except Exception:
            logger.debug("系统通知失败", exc_info=True)
            return False

    def stop(self) -> None:
        icon, self._icon = self._icon, None
        self._started = False
        self._recording = False
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                logger.debug("停止托盘图标失败", exc_info=True)
