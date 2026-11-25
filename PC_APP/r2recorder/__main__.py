"""程序入口：python -m r2recorder 或 python main.py。"""

from __future__ import annotations

import logging
import subprocess
import sys

from . import __version__
from .config import ensure_env_file
from .paths import APP_DIR, LOG_PATH, ensure_dirs
from .ui.main_window import MainWindow
from .ui.theme import LIGHT

logger = logging.getLogger(__name__)


def _enable_dpi_awareness() -> None:
    """Windows 高分屏下避免界面发虚。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _setup_logging() -> None:
    ensure_dirs()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)


def _set_app_user_model_id() -> None:
    """让任务栏把本程序当作独立应用，显示自己的窗口图标。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "OSSRecorder.PC"
        )
    except Exception:
        pass


def _launch_command() -> list[str]:
    """重启时的启动命令：打包后直接运行 exe，源码运行用 -m r2recorder。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, *sys.argv[1:]]
    return [sys.executable, "-m", "r2recorder", *sys.argv[1:]]


def _launch_restart() -> None:
    subprocess.Popen(_launch_command(), cwd=str(APP_DIR), close_fds=True)


def main() -> int:
    _set_app_user_model_id()
    _enable_dpi_awareness()
    _setup_logging()
    ensure_env_file()
    logger.info("启动 OSS 录音机 %s", __version__)
    try:
        window = MainWindow(LIGHT)
        window.mainloop()
    except Exception:
        logger.exception("程序异常退出")
        raise
    if getattr(window, "restart_requested", False):
        logger.info("正在重启应用")
        try:
            _launch_restart()
        except Exception:
            logger.exception("重启应用失败")
            return 1
    logger.info("已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
