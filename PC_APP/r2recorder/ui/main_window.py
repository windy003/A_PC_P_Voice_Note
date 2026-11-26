"""主窗口：录音、播放、上传状态与设置入口。"""

from __future__ import annotations

import logging
import queue
import threading
import time
import tkinter as tk
import uuid
import webbrowser
from dataclasses import replace
from tkinter import messagebox
from typing import Callable

from .. import __version__, audio, formats
from ..config import read_config
from ..hotkey import (
    SHOW_HOTKEY_ID,
    GlobalHotkey,
    HotkeySpec,
    parse_hotkey,
)
from ..model import (
    Recording,
    RecordingRepository,
    TranscriptStatus,
    UploadStatus,
)
from ..paths import CACHE_DIR, DATA_DIR, RECORDINGS_DIR, ensure_dirs
from ..prefs import (
    DEFAULT_SHOW_HOTKEY,
    Preferences,
    load_preferences,
    save_preferences,
)
from ..stt import STTManager, read_stt_config
from ..sync import SyncManager
from ..upload import UploadManager
from ..util import open_path
from .settings_dialog import SettingsDialog
from .theme import MONO_FONT, Theme, UI_FONT
from .toast import GlobalToast, Toast
from .tray import Tray, icon_image
from .widgets import LinkButton, PillButton, PlaybackProgress, ScrollFrame, StatusChip

try:
    from PIL import ImageTk
except Exception:  # pragma: no cover - Pillow 缺失时跳过窗口图标
    ImageTk = None

logger = logging.getLogger(__name__)

MIN_DURATION_MS = 800
PLAY_PROGRESS_INTERVAL_MS = 200
STATUS_TEXT = {
    UploadStatus.LOCAL: "待上传",
    UploadStatus.UPLOADING: "上传中",
    UploadStatus.UPLOADED: "已上传",
    UploadStatus.FAILED: "上传失败",
}


class MainWindow(tk.Tk):
    def __init__(self, theme: Theme):
        super().__init__()
        ensure_dirs()

        self.theme = theme
        self.repository = RecordingRepository()
        self.recorder = audio.Recorder()
        self.player = audio.Player(CACHE_DIR)
        self.uploader = UploadManager(self.repository, self._config_provider)
        self.sync_manager = SyncManager(
            self.repository,
            self._config_provider,
            self.post,
            self._on_sync_message,
        )

        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self.stt = STTManager(
            self.repository,
            self._stt_config_provider,
            self.post,
            self._on_stt_message,
        )
        recovered_stt = self.stt.recover_interrupted()
        if recovered_stt:
            logger.info("恢复了 %d 条中断的语音识别任务", recovered_stt)
        self._drain_id: str | None = None
        self._state_watch_id: str | None = None
        self._last_window_state = ""
        self._ticker_id: str | None = None
        self._playing_id: str | None = None
        self._play_links: dict[str, LinkButton] = {}
        self._progress_by_id: dict[str, PlaybackProgress] = {}
        self._stt_boxes: dict[str, tk.Frame] = {}
        self._stt_links: dict[str, LinkButton] = {}
        self._copy_links: dict[str, LinkButton] = {}
        self._play_started_at: float | None = None
        self._play_duration_ms = 0
        self._play_progress_job: str | None = None
        self._selected_id: str | None = None
        self._selected_index = 0
        self._list_cards: list[tuple[Recording, tk.Frame]] = []
        self._settings_dialog: SettingsDialog | None = None
        self._record_busy = False
        self._closed = False
        self._tray_hint_shown = False
        self._sort_descending = False
        self._want_maximized = True
        self.restart_requested = False
        self._status_cache: dict[str, tuple[UploadStatus, str | None]] = {}
        self._last_sync_request = 0.0
        self.show_hotkey: GlobalHotkey | None = None
        self.show_hotkey_spec = self._load_show_hotkey_spec()

        self.title("OSS 录音机")
        self.configure(bg=theme.surface)
        self._apply_window_icon()
        self.minsize(600, 640)
        self._maximize_window()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build()
        self.repository.add_listener(self._on_repository_changed)
        self._refresh_records(self.repository.snapshot())
        self._apply_config_state()
        self._reset_record_ui()
        self.bind("<Control-q>", self._on_quit_hotkey)

        self.tray = Tray(
            self.post,
            on_show=self._show_from_tray,
            on_toggle_record=self._toggle_recording,
            on_quit=self._quit_app,
            on_restart=self._restart_app,
        )
        tray_started = self.tray.start()
        if tray_started:
            # 默认启动即隐藏在系统托盘，只在托盘里安静运行。
            self.withdraw()
            logger.info("已启动并隐藏在系统托盘")
        else:
            logger.warning(
                "系统托盘不可用（缺少 pystray / Pillow），关闭窗口将直接退出"
            )

        self.hotkey = GlobalHotkey(self._on_global_hotkey)
        if self.hotkey.start():
            logger.info("全局快捷键 F9 已启用")
        else:
            logger.warning("全局快捷键 F9 不可用，F9 仅在窗口内有效")

        self._apply_show_hotkey(notify=False)

        recovered = self.uploader.recover_interrupted()
        if recovered:
            logger.info("恢复了 %d 个中断的上传任务", recovered)

        self._drain_id = self.after(100, self._drain_ui_queue)
        self._last_window_state = self.state()
        self._state_watch_id = self.after(400, self._watch_window_state)
        self.after(300, self._auto_enqueue)
        self.after(1300, self._request_cloud_sync)
        show_text = (
            self.show_hotkey_spec.text
            if self.show_hotkey_spec is not None
            else None
        )
        if tray_started:
            hint = (
                "OSS 录音机已在系统托盘运行 · 按 F9 开始录音，"
                "双击托盘图标打开窗口"
            )
            if show_text:
                hint = (
                    "OSS 录音机已在系统托盘运行 · 按 F9 开始录音，"
                    f"按 {show_text} 或双击托盘图标打开窗口"
                )
        else:
            hint = "按 F9 可在任意窗口开始 / 停止录音"
            if show_text:
                hint += f"，按 {show_text} 显示窗口"
        self.after(600, lambda: self._notify(hint))

    # ------------------------------------------------------------------ 界面
    def _build(self) -> None:
        theme = self.theme

        header = tk.Frame(self, bg=theme.surface)
        header.pack(fill="x", padx=24, pady=(18, 6))

        titles = tk.Frame(header, bg=theme.surface)
        titles.pack(side="left", fill="x", expand=True)
        tk.Label(
            titles,
            text="OSS 录音机",
            font=(UI_FONT, 22, "bold"),
            fg=theme.on_surface,
            bg=theme.surface,
            anchor="w",
        ).pack(fill="x")
        self.subtitle = tk.Label(
            titles,
            text="",
            font=(UI_FONT, 9),
            fg=theme.on_surface_variant,
            bg=theme.surface,
            anchor="w",
        )
        self.subtitle.pack(fill="x", pady=(2, 0))

        settings_button = PillButton(
            header,
            "设置",
            self._open_settings,
            theme=theme,
            width=100,
            height=36,
            radius=18,
            fill=theme.card,
            hover_fill=theme.secondary_container,
            text_color=theme.primary,
            outline=theme.outline,
            font=(UI_FONT, 9, "bold"),
        )
        settings_button.pack(side="right", anchor="n")

        self.retry_all_link = LinkButton(
            header,
            "重试全部",
            self._retry_all,
            theme=theme,
            font=(UI_FONT, 9),
        )
        self.retry_all_link.pack(side="right", anchor="n", padx=(0, 10), pady=8)

        # 配置提示条（未配置时显示）
        self.banner = tk.Frame(self, bg=theme.secondary_container)
        banner_inner = tk.Frame(self.banner, bg=theme.secondary_container)
        banner_inner.pack(fill="x", padx=16, pady=10)
        banner_text = tk.Frame(banner_inner, bg=theme.secondary_container)
        banner_text.pack(side="left", fill="x", expand=True)
        tk.Label(
            banner_text,
            text="尚未配置阿里云 OSS",
            font=(UI_FONT, 10, "bold"),
            fg=theme.on_secondary_container,
            bg=theme.secondary_container,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            banner_text,
            text="录音会保存在本机，配置后才能上传。",
            font=(UI_FONT, 8),
            fg=theme.on_secondary_container,
            bg=theme.secondary_container,
            anchor="w",
        ).pack(fill="x", pady=(2, 0))
        LinkButton(
            banner_inner,
            "去配置",
            self._open_settings,
            theme=theme,
            fg=theme.on_secondary_container,
            hover_fg=theme.primary,
            bg=theme.secondary_container,
            font=(UI_FONT, 9, "bold"),
        ).pack(side="right", pady=4)

        # 录音卡片
        self.record_card = tk.Frame(
            self,
            bg=theme.card,
            highlightthickness=1,
            highlightbackground=theme.outline,
        )
        self.record_card.pack(fill="x", padx=24, pady=(14, 0))
        record_body = tk.Frame(self.record_card, bg=theme.card)
        record_body.pack(fill="x", padx=24, pady=(20, 18))

        self.status_label = tk.Label(
            record_body,
            text="准备录音",
            font=(UI_FONT, 10),
            fg=theme.on_surface_variant,
            bg=theme.card,
        )
        self.status_label.pack()

        self.timer_label = tk.Label(
            record_body,
            text="00:00",
            font=(MONO_FONT, 40),
            fg=theme.on_surface,
            bg=theme.card,
        )
        self.timer_label.pack(pady=(2, 12))

        self.record_button = PillButton(
            record_body,
            "开始录音",
            self._toggle_recording,
            theme=theme,
            width=220,
            height=56,
            radius=28,
            font=(UI_FONT, 13, "bold"),
        )
        self.record_button.pack()

        hint = self._record_hint_text()
        if not audio.AUDIO_AVAILABLE:
            hint = (
                "缺少音频库 sounddevice / numpy，无法录音。\n"
                "请在项目文件夹运行：pip install -r requirements.txt"
            )
        self.hint_label = tk.Label(
            record_body,
            text=hint,
            font=(UI_FONT, 8),
            fg=theme.on_surface_variant,
            bg=theme.card,
            justify="center",
            wraplength=560,
        )
        self.hint_label.pack(pady=(12, 0))

        # 列表标题
        list_header = tk.Frame(self, bg=theme.surface)
        list_header.pack(fill="x", padx=24, pady=(18, 6))
        tk.Label(
            list_header,
            text="录音记录",
            font=(UI_FONT, 14, "bold"),
            fg=theme.on_surface,
            bg=theme.surface,
            anchor="w",
        ).pack(side="left")
        self.count_label = tk.Label(
            list_header,
            text="",
            font=(UI_FONT, 9),
            fg=theme.on_surface_variant,
            bg=theme.surface,
            anchor="e",
        )
        self.count_label.pack(side="right")

        self.sort_link = LinkButton(
            list_header,
            self._sort_label(),
            self._toggle_sort,
            theme=theme,
            font=(UI_FONT, 9),
        )
        self.sort_link.pack(side="right", padx=(0, 12))

        # 录音列表
        self.scroll = ScrollFrame(self, theme)
        self.scroll.pack(fill="both", expand=True, padx=24, pady=(0, 6))
        self._setup_list_keyboard()

        # 底部
        footer = tk.Frame(self, bg=theme.surface)
        footer.pack(fill="x", padx=24, pady=(4, 14))
        LinkButton(
            footer,
            "打开录音文件夹",
            lambda: open_path(DATA_DIR),
            theme=theme,
            font=(UI_FONT, 9),
        ).pack(side="left")
        tk.Label(
            footer,
            text=f"v{__version__}",
            font=(UI_FONT, 8),
            fg=theme.on_surface_variant,
            bg=theme.surface,
        ).pack(side="right")

        self.toast = Toast(self, theme)
        self.global_toast = GlobalToast(self, theme)
        self.bind("<F9>", self._on_hotkey)
        self.bind("<Configure>", self._on_window_configure, add="+")
        self.bind("<Map>", self._on_window_map, add="+")

    def _maximize_window(self) -> None:
        """默认以最大化状态打开窗口。"""
        self.update_idletasks()
        try:
            self.state("zoomed")
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                pass

    def _on_window_configure(self, event) -> None:
        """记录用户手动最大化 / 还原的状态，下次唤起时保持一致。"""
        if event.widget is not self or self._closed:
            return
        try:
            if not self.winfo_viewable():
                return
            state = self.state()
        except tk.TclError:
            return
        if state in ("normal", "zoomed"):
            self._want_maximized = state == "zoomed"

    def _apply_window_icon(self) -> None:
        """任务栏 / 标题栏图标使用与托盘一致的麦克风图标。"""
        if ImageTk is None:
            return
        photos = []
        for size in (64, 48, 32, 16):
            image = icon_image(size)
            if image is None:
                continue
            try:
                photos.append(ImageTk.PhotoImage(image))
            except Exception:
                logger.debug("生成窗口图标失败", exc_info=True)
        if not photos:
            return
        try:
            self.iconphoto(True, *photos)
        except tk.TclError:
            logger.debug("设置窗口图标失败", exc_info=True)
            return
        self._window_icons = photos

    # ------------------------------------------------------------------ 线程桥接
    def post(self, callback: Callable[[], None]) -> None:
        """线程安全的界面更新入口：把回调放入主线程队列。"""
        self._ui_queue.put(callback)

    def _drain_ui_queue(self) -> None:
        self._drain_id = None
        if self._closed:
            return
        while True:
            try:
                callback = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:
                logger.exception("界面更新回调执行失败")
        self._drain_id = self.after(100, self._drain_ui_queue)

    def report_callback_exception(self, exc, value, tb) -> None:  # noqa: N802
        logger.error("界面回调异常", exc_info=(exc, value, tb))

    # ------------------------------------------------------------------ 配置
    def _config_provider(self):
        config = read_config()
        return config if config.is_complete else None

    def _stt_config_provider(self):
        config = read_stt_config()
        return config if config.is_complete else None

    def _require_stt_config(self) -> bool:
        if read_stt_config().is_complete:
            return True
        if messagebox.askyesno(
            "语音识别设置",
            "请先在设置中填写语音识别的 API Key。现在去设置吗？",
            parent=self,
        ):
            self._open_settings()
        return False

    def _apply_config_state(self) -> None:
        if read_config().is_complete:
            self.banner.pack_forget()
            self.subtitle.configure(text="本机录音，自动上传到阿里云 OSS")
        else:
            self.banner.pack(
                fill="x",
                padx=24,
                pady=(6, 0),
                before=self.record_card,
            )
            self.subtitle.configure(text="尚未配置阿里云 OSS，录音只会保存在本机")

    def _require_config(self) -> bool:
        if read_config().is_complete:
            return True
        if messagebox.askyesno(
            "阿里云 OSS 设置",
            "请先在 OSS 设置中填写配置。现在去设置吗？",
            parent=self,
        ):
            self._open_settings()
        return False

    def _open_settings(self) -> None:
        if self._settings_dialog is not None:
            try:
                self._settings_dialog.lift()
                self._settings_dialog.focus_set()
                return
            except tk.TclError:
                self._settings_dialog = None
        self._settings_dialog = SettingsDialog(
            self,
            self.theme,
            post=self.post,
            on_saved=self._on_settings_saved,
            on_closed=self._on_settings_closed,
        )

    def _on_settings_saved(self) -> None:
        self._apply_config_state()
        self._apply_show_hotkey()
        self.after(100, self._auto_enqueue)
        self.sync_manager.request_sync(notify=True)

    def _on_settings_closed(self) -> None:
        self._settings_dialog = None

    def _on_sync_message(
        self,
        text: str,
        error: bool,
        include_total: bool = True,
    ) -> None:
        if self._closed:
            return
        self._notify(
            text,
            error=error,
            duration_ms=5000 if error else 4000,
            include_total=include_total,
        )

    def _on_stt_message(
        self,
        text: str,
        error: bool,
        recording_id: str | None = None,
    ) -> None:
        if self._closed:
            return
        self._notify(
            text,
            error=error,
            duration_ms=5000 if error else 4000,
        )
        if not error and recording_id:
            self.after_idle(
                lambda: self._scroll_recording_into_view(recording_id)
            )

    def _scroll_recording_into_view(self, recording_id: str) -> None:
        for recording, card in self._list_cards:
            if recording.id == recording_id:
                self._scroll_card_into_view(card)
                return

    def _request_cloud_sync(self) -> None:
        """打开应用、从托盘唤醒或窗口重新显示时触发一次同步（2 秒内去重）。"""
        if self._closed:
            return
        now = time.monotonic()
        if now - self._last_sync_request < 2.0:
            return
        self._last_sync_request = now
        self.sync_manager.request_sync(notify=True)

    def _on_window_map(self, event) -> None:
        if event.widget is not self or self._closed:
            return
        self._request_cloud_sync()

    def _watch_window_state(self) -> None:
        """轮询窗口状态：从任务栏最小化恢复时 Windows 不一定触发 <Map>。"""
        self._state_watch_id = None
        if self._closed:
            return
        try:
            state = self.state()
        except tk.TclError:
            return
        previous, self._last_window_state = self._last_window_state, state
        if previous == "iconic" and state in ("normal", "zoomed"):
            self._request_cloud_sync()
        self._state_watch_id = self.after(400, self._watch_window_state)

    # ------------------------------------------------------------------ 快捷键
    def _load_show_hotkey_spec(self) -> HotkeySpec | None:
        text = load_preferences().show_hotkey.strip()
        if not text:
            return None
        try:
            return parse_hotkey(text)
        except ValueError as exc:
            logger.warning(
                "显示窗口快捷键设置无效（%s），改用默认值 %s",
                exc,
                DEFAULT_SHOW_HOTKEY,
            )
            try:
                return parse_hotkey(DEFAULT_SHOW_HOTKEY)
            except ValueError:
                return None

    def _apply_show_hotkey(self, *, notify: bool = True) -> None:
        """读取偏好设置并重新注册“显示窗口”快捷键。"""
        spec = self._load_show_hotkey_spec()
        current = self.show_hotkey
        if current is not None and current.active and spec == current.spec:
            self.show_hotkey_spec = spec
            self._update_hotkey_hint()
            return

        new_hotkey = None
        if spec is not None:
            candidate = GlobalHotkey(
                self._on_show_hotkey,
                spec,
                SHOW_HOTKEY_ID,
                "显示窗口快捷键",
            )
            if not candidate.start():
                logger.warning("显示窗口快捷键 %s 注册失败", spec.text)
                if current is not None and current.active:
                    # 新快捷键不可用时继续沿用原来的，并把设置改回去。
                    try:
                        save_preferences(
                            Preferences(show_hotkey=current.spec.text)
                        )
                    except Exception:
                        logger.debug("写回快捷键设置失败", exc_info=True)
                    if notify:
                        self._notify(
                            f"快捷键 {spec.text} 不可用，仍使用 {current.spec.text}",
                            error=True,
                            duration_ms=5000,
                        )
                elif notify:
                    self._notify(
                        f"无法注册快捷键 {spec.text}，可能已被其他程序占用",
                        error=True,
                        duration_ms=5000,
                    )
                return
            new_hotkey = candidate

        self.show_hotkey_spec = spec
        self.show_hotkey = new_hotkey
        if current is not None:
            current.stop()
        if notify:
            if spec is not None:
                self._notify(f"显示窗口快捷键：{spec.text}")
            else:
                self._notify("已关闭显示窗口快捷键")
        self._update_hotkey_hint()

    def _on_show_hotkey(self) -> None:
        if self._closed:
            return
        logger.info("收到显示窗口快捷键")
        self.post(self._show_from_tray)

    def _record_hint_text(self) -> str:
        text = (
            f"录音将以 {audio.recording_format_label()} 格式保存在本机，"
            "配置成功后自动上传到阿里云 OSS。"
            "全局快捷键：F9 开始 / 停止"
        )
        spec = self.show_hotkey_spec
        if spec is not None:
            text += f"；{spec.text} 显示窗口"
        return text + "。"

    def _update_hotkey_hint(self) -> None:
        label = getattr(self, "hint_label", None)
        if label is None or not audio.AUDIO_AVAILABLE:
            return
        try:
            label.configure(text=self._record_hint_text())
        except tk.TclError:
            pass

    def _auto_enqueue(self) -> None:
        if self._closed or not read_config().is_complete:
            return
        count = self.uploader.enqueue_pending_local()
        if count:
            logger.info("已将 %d 条待上传录音加入队列", count)

    # ------------------------------------------------------------------ 录音
    def _on_global_hotkey(self) -> None:
        if self._closed:
            return
        logger.info("收到全局快捷键 F9")
        self.post(self._toggle_recording)

    def _on_hotkey(self, _event=None) -> str:
        if self.hotkey.active:
            return "break"
        if self._settings_dialog is not None:
            return "break"
        self._toggle_recording()
        return "break"

    def _toggle_recording(self) -> None:
        if self._record_busy:
            self._notify("正在保存录音，请稍候…")
            return
        if self.recorder.active:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        try:
            self.recorder.start()
        except audio.AudioError as exc:
            logger.warning("无法开始录音：%s", exc)
            self._notify(f"无法开始录音：{exc}", error=True, duration_ms=5000)
            return
        except Exception as exc:
            logger.exception("开始录音失败")
            self._notify(f"无法开始录音：{exc}", error=True, duration_ms=5000)
            return

        logger.info("开始录音")
        theme = self.theme
        self.status_label.configure(text="正在录音…", fg=theme.record_red)
        self.timer_label.configure(text="00:00", fg=theme.on_surface)
        self.record_button.set_text("停止并保存")
        self.record_button.set_colors(
            theme.record_red,
            theme.record_red_hover,
            theme.on_record,
        )
        self.tray.set_recording(True)
        self._notify(
            "开始录音 · 按 F9 停止" if self._is_visible() else "开始录音"
        )
        self._tick()

    def _tick(self) -> None:
        self._ticker_id = None
        if not self.recorder.active:
            return
        self.timer_label.configure(text=formats.duration(self.recorder.elapsed_ms))
        self._ticker_id = self.after(250, self._tick)

    def _cancel_ticker(self) -> None:
        if self._ticker_id is not None:
            try:
                self.after_cancel(self._ticker_id)
            except tk.TclError:
                pass
            self._ticker_id = None

    def _stop_recording(self) -> None:
        started_at_ms = self.recorder.started_at_ms
        data, samplerate, duration_ms = self.recorder.stop()
        logger.info("停止录音，时长 %d ms", duration_ms)
        self.tray.set_recording(False)
        self._cancel_ticker()
        self.timer_label.configure(text=formats.duration(duration_ms))

        if data is None or len(data) == 0 or duration_ms < MIN_DURATION_MS:
            self._reset_record_ui()
            self._notify("录音时间太短，已丢弃", error=True)
            return

        self._set_record_busy(True)
        threading.Thread(
            target=self._save_worker,
            args=(data, samplerate, duration_ms, started_at_ms),
            name="oss-save",
            daemon=True,
        ).start()

    def _save_worker(self, data, samplerate: int, duration_ms: int, created_at: int) -> None:
        try:
            recording = _write_recording(data, samplerate, duration_ms, created_at)
        except Exception as exc:
            logger.exception("保存录音失败")
            self.post(lambda: self._on_save_failed(exc))
            return
        self.post(lambda: self._on_saved(recording))

    def _set_record_busy(self, busy: bool) -> None:
        self._record_busy = busy
        theme = self.theme
        self.record_button.set_enabled(not busy)
        if busy:
            self.record_button.set_text("正在保存…")
            self.status_label.configure(text="正在保存…", fg=theme.on_surface_variant)
            self.timer_label.configure(fg=theme.on_surface_variant)
        else:
            self.record_button.set_text("开始录音")
            self.record_button.set_colors(
                theme.primary,
                theme.primary_hover,
                theme.on_primary,
            )

    def _reset_record_ui(self) -> None:
        theme = self.theme
        self._record_busy = False
        self._cancel_ticker()
        self.timer_label.configure(text="00:00", fg=theme.on_surface)
        self.status_label.configure(text="准备录音", fg=theme.on_surface_variant)
        self.record_button.set_text("开始录音")
        self.record_button.set_colors(
            theme.primary,
            theme.primary_hover,
            theme.on_primary,
        )
        self.record_button.set_enabled(audio.AUDIO_AVAILABLE)

    def _on_saved(self, recording: Recording) -> None:
        self._set_record_busy(False)
        self._reset_record_ui()
        self.repository.add(recording)
        if read_config().is_complete:
            self.uploader.enqueue(recording.id)
            self._notify("录音已保存，正在上传…")
        else:
            self._notify("录音已保存（待上传）")
        stt_config = read_stt_config()
        if stt_config.auto and stt_config.is_complete:
            self.stt.enqueue(recording.id)

    def _on_save_failed(self, exc: Exception) -> None:
        self._set_record_busy(False)
        self._reset_record_ui()
        self._notify(
            f"保存失败：{exc}" if str(exc) else "保存失败",
            error=True,
            duration_ms=5000,
        )

    # ------------------------------------------------------------------ 列表
    def _on_repository_changed(self, _items) -> None:
        self.post(self._apply_repository_update)

    def _apply_repository_update(self) -> None:
        if self._closed:
            return
        items = self.repository.snapshot()
        if self._apply_transcript_update(items):
            return
        self._refresh_records(items)

    def _apply_transcript_update(self, items: list[Recording]) -> bool:
        """只有识别字段变化时就地刷新卡片，避免整个列表重建闪烁。"""
        if not items and not self._list_cards:
            return True
        ordered = self._sort_recordings(items)
        if [item.id for item in ordered] != [
            recording.id for recording, _card in self._list_cards
        ]:
            return False

        current = {
            recording.id: recording for recording, _card in self._list_cards
        }
        transcript_fields = (
            "transcript",
            "transcript_status",
            "transcript_error",
            "transcript_model",
            "transcript_updated_at",
        )
        changed: list[Recording] = []
        for item in ordered:
            previous = current[item.id]
            if _without_transcript(previous) != _without_transcript(item):
                return False
            if any(
                getattr(previous, field) != getattr(item, field)
                for field in transcript_fields
            ):
                changed.append(item)

        self._list_cards = [
            (item, card)
            for item, (_previous, card) in zip(ordered, self._list_cards)
        ]
        for item in changed:
            self._update_transcript_card(item)
        return True

    def _update_transcript_card(self, recording: Recording) -> None:
        box = self._stt_boxes.get(recording.id)
        if box is not None:
            try:
                if box.winfo_exists():
                    self._render_stt_box(box, recording)
            except tk.TclError:
                pass
        link = self._stt_links.get(recording.id)
        if link is not None:
            try:
                link.set_text(self._stt_link_text(recording))
                link.set_enabled(
                    recording.transcript_status != TranscriptStatus.PENDING
                    and recording.local_path.exists()
                )
            except tk.TclError:
                pass
        copy_link = self._copy_links.get(recording.id)
        if copy_link is not None:
            try:
                copy_link.set_enabled(bool(recording.transcript))
            except tk.TclError:
                pass

    @staticmethod
    def _stt_link_text(recording: Recording) -> str:
        if recording.transcript_status == TranscriptStatus.PENDING:
            return "识别中…"
        return "重新识别" if recording.transcript else "识别文字"

    def _refresh_records(self, items: list[Recording]) -> None:
        if self._closed:
            return
        ordered = self._sort_recordings(items)
        previous_id = self._selected_id
        previous_index = self._selected_index
        position = self.scroll.view_position()
        for child in self.scroll.inner.winfo_children():
            child.destroy()
        self._list_cards = []
        self._play_links = {}
        self._progress_by_id = {}
        self._stt_boxes = {}
        self._stt_links = {}
        self._copy_links = {}

        if not ordered:
            tk.Label(
                self.scroll.inner,
                text="还没有录音\n点击上方按钮开始",
                font=(UI_FONT, 11),
                fg=self.theme.on_surface_variant,
                bg=self.theme.surface,
                justify="center",
            ).pack(pady=60)
        else:
            for recording in ordered:
                self._build_card(self.scroll.inner, recording)

        self.count_label.configure(text=f"{len(ordered)} 条" if ordered else "")
        self.scroll.canvas.yview_moveto(position)
        self._restore_list_selection(previous_id, previous_index)
        self._restore_play_progress()
        self._notify_status_changes(ordered)

    # ------------------------------------------------------------------ 列表键盘
    def _setup_list_keyboard(self) -> None:
        canvas = self.scroll.canvas
        canvas.configure(takefocus=1)
        # 让 Tab 直接落到列表画布，而不是先停在被隐藏的滚动条上。
        try:
            self.scroll.vsb.configure(takefocus=0)
        except tk.TclError:
            pass
        canvas.bind("<Up>", self._on_list_up)
        canvas.bind("<Down>", self._on_list_down)
        canvas.bind("<space>", self._on_list_recognize)
        canvas.bind("<Shift-space>", self._on_list_toggle_play)
        canvas.bind("<Delete>", self._on_list_delete)
        canvas.bind("<KP_Delete>", self._on_list_delete)
        canvas.bind("<FocusIn>", self._on_list_focus)
        # 焦点不在列表上（例如停在按钮或窗口本身）时，方向键仍然直接导航列表，
        # 不必先按 Tab 把焦点移到列表。
        self.bind("<Up>", self._on_list_up, add="+")
        self.bind("<Down>", self._on_list_down, add="+")

    def _on_list_focus(self, _event=None) -> None:
        if not self._list_cards:
            return
        if self._selected_id is None:
            self._select_list_index(0, focus=False)
        else:
            self._scroll_selected_into_view()

    def _on_list_up(self, _event=None) -> str:
        if self._list_cards:
            current = self._current_list_index()
            target = current - 1 if current >= 0 else len(self._list_cards) - 1
            self._select_list_index(target)
        return "break"

    def _on_list_down(self, _event=None) -> str:
        if self._list_cards:
            current = self._current_list_index()
            target = current + 1 if current >= 0 else 0
            self._select_list_index(target)
        return "break"

    def _on_list_recognize(self, _event=None) -> str:
        recording = self._selected_recording()
        if recording is not None:
            self._recognize(recording)
        return "break"

    def _on_list_toggle_play(self, _event=None) -> str:
        recording = self._selected_recording()
        if recording is not None:
            self._toggle_play(recording)
        return "break"

    def _on_list_delete(self, _event=None) -> str:
        recording = self._selected_recording()
        if recording is not None:
            self._delete(recording)
            self.after_idle(self._focus_list)
        return "break"

    def _focus_list(self) -> None:
        try:
            self.scroll.canvas.focus_set()
        except tk.TclError:
            pass

    def _current_list_index(self) -> int:
        if self._selected_id is None:
            return -1
        for index, (recording, _card) in enumerate(self._list_cards):
            if recording.id == self._selected_id:
                return index
        return -1

    def _selected_recording(self) -> Recording | None:
        index = self._current_list_index()
        if index < 0:
            return None
        return self._list_cards[index][0]

    def _select_list_index(self, index: int, *, focus: bool = False) -> None:
        if not self._list_cards:
            self._selected_id = None
            return
        index = max(0, min(index, len(self._list_cards) - 1))
        self._selected_index = index
        self._selected_id = self._list_cards[index][0].id
        self._apply_list_selection()
        self._scroll_card_into_view(self._list_cards[index][1])
        if focus:
            self.scroll.canvas.focus_set()

    def _restore_list_selection(self, previous_id: str | None, previous_index: int) -> None:
        if not self._list_cards:
            self._selected_id = None
            self._selected_index = 0
            return
        if previous_id is None:
            self._apply_list_selection()
            return

        index = next(
            (
                i
                for i, (recording, _card) in enumerate(self._list_cards)
                if recording.id == previous_id
            ),
            None,
        )
        if index is not None:
            self._selected_index = index
            self._selected_id = previous_id
            self._apply_list_selection()
            return

        # 原来选中的条目已被删除：选中删除位置上的下一条（最后一条则选前一条）。
        index = max(0, min(previous_index, len(self._list_cards) - 1))
        self._selected_index = index
        self._selected_id = self._list_cards[index][0].id
        self._apply_list_selection()
        self.after_idle(self._scroll_selected_into_view)

    def _scroll_selected_into_view(self) -> None:
        index = self._current_list_index()
        if index >= 0:
            self._scroll_card_into_view(self._list_cards[index][1])

    def _apply_list_selection(self) -> None:
        for recording, card in self._list_cards:
            selected = recording.id == self._selected_id
            try:
                card.configure(
                    highlightbackground=(
                        self.theme.primary if selected else self.theme.outline
                    )
                )
            except tk.TclError:
                pass

    def _scroll_card_into_view(self, card: tk.Frame) -> None:
        try:
            canvas = self.scroll.canvas
            canvas.update_idletasks()
            bbox = canvas.bbox("all")
            if bbox is None:
                return
            content_height = bbox[3] - bbox[1]
            viewport_height = canvas.winfo_height()
            if content_height <= 0 or viewport_height <= 0:
                return
            top = card.winfo_y() - bbox[1]
            bottom = top + card.winfo_height()
            view_top = canvas.canvasy(0)
            view_bottom = view_top + viewport_height
            margin = 6
            if top < view_top + margin:
                canvas.yview_moveto(max(0.0, (top - margin) / content_height))
            elif bottom > view_bottom - margin:
                canvas.yview_moveto(
                    max(0.0, (bottom - viewport_height + margin) / content_height)
                )
        except tk.TclError:
            pass

    def _sort_label(self) -> str:
        return "时间倒序 ↓" if self._sort_descending else "时间正序 ↑"

    def _sort_recordings(self, items: list[Recording]) -> list[Recording]:
        def sort_key(recording: Recording) -> tuple[int, str]:
            stamp = formats.filename_time_ms(recording.file_name)
            return (
                stamp if stamp is not None else recording.created_at,
                recording.file_name,
            )

        return sorted(items, key=sort_key, reverse=self._sort_descending)

    def _toggle_sort(self) -> None:
        self._sort_descending = not self._sort_descending
        self.sort_link.set_text(self._sort_label())
        self._refresh_records(self.repository.snapshot())
        self.scroll.scroll_to_top()

    def _notify_status_changes(self, items: list[Recording]) -> None:
        for recording in items:
            previous = self._status_cache.get(recording.id)
            if previous is not None:
                previous_status, _previous_error = previous
                if (
                    recording.status == UploadStatus.UPLOADED
                    and previous_status != UploadStatus.UPLOADED
                ):
                    self._notify(
                        "已上传到 OSS：" + formats.display_name(recording.created_at)
                    )
                elif (
                    recording.status == UploadStatus.FAILED
                    and previous_status != UploadStatus.FAILED
                    and not (recording.error and "自动重试" in recording.error)
                ):
                    self._notify(
                        "上传失败，可在列表中查看原因",
                        error=True,
                        duration_ms=4000,
                    )
        self._status_cache = {
            item.id: (item.status, item.error) for item in items
        }

    def _build_card(self, parent: tk.Misc, recording: Recording) -> None:
        theme = self.theme
        card = tk.Frame(
            parent,
            bg=theme.card,
            highlightthickness=2,
            highlightbackground=theme.outline,
        )
        card.pack(fill="x", pady=(0, 10))
        body = tk.Frame(card, bg=theme.card)
        body.pack(fill="x", padx=14, pady=(12, 6))

        top = tk.Frame(body, bg=theme.card)
        top.pack(fill="x")
        tk.Label(
            top,
            text=formats.display_name(recording.created_at),
            font=(UI_FONT, 11, "bold"),
            fg=theme.on_surface,
            bg=theme.card,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        chip = StatusChip(top, theme)
        text, color = _status_view(theme, recording.status)
        chip.set(text, color)
        chip.pack(side="right", padx=(8, 0))

        meta = " · ".join(
            [
                formats.date_time(recording.created_at),
                formats.duration(recording.duration_ms),
                formats.size(recording.size_bytes),
                recording.file_name,
            ]
        )
        tk.Label(
            body,
            text=meta,
            font=(UI_FONT, 8),
            fg=theme.on_surface_variant,
            bg=theme.card,
            anchor="w",
            justify="left",
            wraplength=640,
        ).pack(fill="x", pady=(3, 0))

        if recording.error:
            tk.Label(
                body,
                text=recording.error,
                font=(UI_FONT, 8),
                fg=theme.error,
                bg=theme.card,
                anchor="w",
                justify="left",
                wraplength=640,
            ).pack(fill="x", pady=(3, 0))

        stt_box = tk.Frame(body, bg=theme.card)
        stt_box.pack(fill="x")
        self._stt_boxes[recording.id] = stt_box
        self._render_stt_box(stt_box, recording)

        actions = tk.Frame(body, bg=theme.card)
        actions.pack(fill="x", pady=(4, 0))

        LinkButton(
            actions,
            "删除",
            lambda: self._delete(recording),
            theme=theme,
            fg=theme.error,
            hover_fg=theme.error,
            font=(UI_FONT, 9),
        ).pack(side="right")

        if recording.status in (UploadStatus.LOCAL, UploadStatus.FAILED):
            LinkButton(
                actions,
                "重试",
                lambda: self._retry(recording),
                theme=theme,
                font=(UI_FONT, 9),
            ).pack(side="right", padx=(0, 8))

        if recording.remote_url:
            LinkButton(
                actions,
                "打开",
                lambda: self._open_remote(recording),
                theme=theme,
                font=(UI_FONT, 9),
            ).pack(side="right", padx=(0, 8))

        play_link = LinkButton(
            actions,
            "停止" if self._playing_id == recording.id else "播放",
            lambda: self._toggle_play(recording),
            theme=theme,
            font=(UI_FONT, 9),
        )
        play_link.pack(side="right", padx=(0, 8))
        play_link.set_enabled(recording.local_path.exists())
        self._play_links[recording.id] = play_link

        copy_link = LinkButton(
            actions,
            "复制文字",
            lambda: self._copy_transcript_by_id(recording.id),
            theme=theme,
            font=(UI_FONT, 9),
        )
        copy_link.pack(side="right", padx=(0, 8))
        copy_link.set_enabled(bool(recording.transcript))
        self._copy_links[recording.id] = copy_link

        stt_link = LinkButton(
            actions,
            self._stt_link_text(recording),
            lambda: self._recognize_by_id(recording.id),
            theme=theme,
            font=(UI_FONT, 9),
        )
        stt_link.pack(side="right", padx=(0, 8))
        stt_link.set_enabled(
            recording.transcript_status != TranscriptStatus.PENDING
            and recording.local_path.exists()
        )
        self._stt_links[recording.id] = stt_link

        progress = PlaybackProgress(card, theme)
        progress.place(x=0, rely=1.0, anchor="sw", relwidth=1.0)
        progress.place_forget()
        self._progress_by_id[recording.id] = progress

        self._list_cards.append((recording, card))

    def _render_stt_box(self, box: tk.Misc, recording: Recording) -> None:
        """只重建卡片内部的识别区域，不触碰整个列表。"""
        for child in box.winfo_children():
            child.destroy()
        theme = self.theme
        if recording.transcript:
            transcript_box = tk.Frame(box, bg=theme.secondary_container)
            transcript_box.pack(fill="x", pady=(6, 0))
            tk.Label(
                transcript_box,
                text=recording.transcript,
                font=(UI_FONT, 9),
                fg=theme.on_secondary_container,
                bg=theme.secondary_container,
                anchor="w",
                justify="left",
                wraplength=620,
                padx=12,
                pady=8,
            ).pack(fill="x")
        elif recording.transcript_status == TranscriptStatus.PENDING:
            tk.Label(
                box,
                text="正在识别语音…",
                font=(UI_FONT, 8),
                fg=theme.on_surface_variant,
                bg=theme.card,
                anchor="w",
            ).pack(fill="x", pady=(3, 0))
        if (
            recording.transcript_status == TranscriptStatus.FAILED
            and recording.transcript_error
        ):
            tk.Label(
                box,
                text="识别失败：" + recording.transcript_error,
                font=(UI_FONT, 8),
                fg=theme.error,
                bg=theme.card,
                anchor="w",
                justify="left",
                wraplength=640,
            ).pack(fill="x", pady=(3, 0))

    # ------------------------------------------------------------------ 操作
    def _update_play_links(self) -> None:
        """只刷新播放/停止文字，避免重建整个列表导致闪烁。"""
        for recording_id, link in self._play_links.items():
            try:
                link.set_text(
                    "停止" if recording_id == self._playing_id else "播放"
                )
            except tk.TclError:
                pass

    def _toggle_play(self, recording: Recording) -> None:
        if self._playing_id == recording.id:
            self.player.stop()
            self._playing_id = None
            self._update_play_links()
            self._stop_play_progress()
            return

        path = recording.local_path
        if not path.exists():
            messagebox.showwarning("无法播放", "本地录音文件不存在。", parent=self)
            return

        self._stop_play_progress()
        duration_ms = recording.duration_ms
        if duration_ms <= 0:
            try:
                duration_ms = audio.probe_duration_ms(path)
            except Exception:
                duration_ms = 0
        self._play_duration_ms = max(0, int(duration_ms))
        self._playing_id = recording.id
        self._update_play_links()
        try:
            self.player.play(
                path,
                lambda error: self.post(
                    lambda: self._on_play_finished(recording.id, error)
                ),
                lambda: self.post(
                    lambda: self._on_play_started(recording.id)
                ),
            )
        except audio.AudioError as exc:
            self._playing_id = None
            self._update_play_links()
            self._stop_play_progress()
            messagebox.showerror("无法播放", str(exc), parent=self)
        except Exception as exc:
            logger.exception("播放失败")
            self._playing_id = None
            self._update_play_links()
            self._stop_play_progress()
            messagebox.showerror("无法播放", str(exc), parent=self)

    def _on_play_started(self, recording_id: str) -> None:
        if self._playing_id != recording_id:
            return
        self._play_started_at = time.monotonic()
        self._place_play_progress(recording_id, 0.0)
        self._schedule_play_progress_tick()

    def _on_play_finished(self, recording_id: str, error: str | None) -> None:
        if self._playing_id != recording_id:
            return
        self._playing_id = None
        self._update_play_links()
        self._stop_play_progress()
        if error:
            messagebox.showerror("无法播放", error, parent=self)

    def _place_play_progress(
        self,
        recording_id: str,
        progress: float | None = None,
    ) -> None:
        widget = self._progress_by_id.get(recording_id)
        if widget is None:
            return
        try:
            if progress is not None:
                widget.set_progress(progress)
            widget.place(x=0, rely=1.0, anchor="sw", relwidth=1.0)
            widget.lift()
        except tk.TclError:
            pass

    def _restore_play_progress(self) -> None:
        """列表重建后，把正在播放条目的进度条重新挂到新卡片上。"""
        recording_id = self._playing_id
        started_at = self._play_started_at
        if recording_id is None or started_at is None:
            return
        progress = 0.0
        if self._play_duration_ms > 0:
            progress = min(
                1.0,
                ((time.monotonic() - started_at) * 1000.0)
                / self._play_duration_ms,
            )
        self._place_play_progress(recording_id, progress)

    def _schedule_play_progress_tick(self) -> None:
        self._cancel_play_progress_tick()
        self._play_progress_job = self.after(
            PLAY_PROGRESS_INTERVAL_MS,
            self._tick_play_progress,
        )

    def _cancel_play_progress_tick(self) -> None:
        if self._play_progress_job is None:
            return
        try:
            self.after_cancel(self._play_progress_job)
        except tk.TclError:
            pass
        self._play_progress_job = None

    def _tick_play_progress(self) -> None:
        self._play_progress_job = None
        recording_id = self._playing_id
        started_at = self._play_started_at
        if recording_id is None or started_at is None:
            return
        progress = 0.0
        if self._play_duration_ms > 0:
            progress = min(
                1.0,
                ((time.monotonic() - started_at) * 1000.0)
                / self._play_duration_ms,
            )
        widget = self._progress_by_id.get(recording_id)
        if widget is not None:
            try:
                widget.set_progress(progress)
            except tk.TclError:
                pass
        self._schedule_play_progress_tick()

    def _stop_play_progress(self) -> None:
        self._cancel_play_progress_tick()
        self._play_started_at = None
        self._play_duration_ms = 0
        for widget in self._progress_by_id.values():
            try:
                widget.reset()
                widget.place_forget()
            except tk.TclError:
                pass

    def _open_remote(self, recording: Recording) -> None:
        if not recording.remote_url:
            return
        try:
            webbrowser.open(recording.remote_url)
        except Exception as exc:
            logger.exception("打开链接失败")
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _recognize_by_id(self, recording_id: str) -> None:
        recording = self.repository.get(recording_id)
        if recording is not None:
            self._recognize(recording)

    def _copy_transcript_by_id(self, recording_id: str) -> None:
        recording = self.repository.get(recording_id)
        if recording is not None:
            self._copy_transcript(recording)

    def _recognize(self, recording: Recording) -> None:
        if recording.transcript_status == TranscriptStatus.PENDING:
            self._notify("正在识别中，请稍候…", include_total=False)
            return
        if not self._require_stt_config():
            return
        if not recording.local_path.exists():
            self._notify("本地录音文件不存在，无法识别", error=True)
            return
        force = bool(recording.transcript)
        if not self.stt.enqueue(recording.id, force=force):
            self._notify("无法开始识别，请稍后再试", error=True)
            return
        self._notify("正在识别语音…", include_total=False)

    def _copy_transcript(self, recording: Recording) -> None:
        text = (recording.transcript or "").strip()
        if not text:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
        except tk.TclError:
            self._notify("复制失败：无法访问剪贴板", error=True)
            return
        self._notify("文字已复制到剪贴板", include_total=False)

    def _retry(self, recording: Recording) -> None:
        if not self._require_config():
            return
        self.repository.update(
            recording.id,
            lambda item: replace(item, status=UploadStatus.LOCAL, error=None),
        )
        self.uploader.enqueue(recording.id, force=True)
        self._notify("已加入上传队列")

    def _retry_all(self) -> None:
        if not self._require_config():
            return
        count = self.uploader.retry_all()
        if count == 0:
            self._notify("没有需要重试的录音")
        else:
            self._notify(f"已将 {count} 条录音加入上传队列")

    def _delete(self, recording: Recording) -> None:
        if not messagebox.askyesno(
            "删除录音",
            "将同时删除本机和阿里云 OSS 上的文件，其他设备下次同步时也会删除。"
            "确定删除吗？",
            parent=self,
        ):
            return
        if self._playing_id == recording.id:
            self.player.stop()
            self._playing_id = None
            self._update_play_links()
            self._stop_play_progress()
        try:
            recording.local_path.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "删除录音文件失败：%s", recording.local_path, exc_info=True
            )
        self.repository.delete(recording.id)
        logger.info("删除录音：%s", recording.file_name)

        was_remote = bool(recording.object_key) or recording.status in (
            UploadStatus.UPLOADING,
            UploadStatus.UPLOADED,
        )
        if was_remote:
            key = recording.object_key or read_config().object_key_for(
                recording.file_name
            )
            label = formats.display_name(recording.created_at)
            local_count = len(self.repository.snapshot())
            self._notify(
                f"本地删除成功，正在删除云端 · 本地 {local_count} 条",
                include_total=False,
            )
            self.sync_manager.delete_remote(key, label)
        else:
            local_count = len(self.repository.snapshot())
            self._notify(
                f"本地删除成功（未上传过） · 本地 {local_count} 条",
                include_total=False,
            )

    # ------------------------------------------------------------------ 托盘 / 退出
    def _is_visible(self) -> bool:
        try:
            return self.state() in ("normal", "zoomed") and bool(
                self.winfo_viewable()
            )
        except tk.TclError:
            return False

    def _notify(
        self,
        text: str,
        *,
        error: bool = False,
        duration_ms: int = 3000,
        include_total: bool = True,
    ) -> None:
        """屏幕级全局提示：主窗口隐藏、其他程序在前台时也会显示。"""
        if include_total:
            text = self._with_total_count(text)
        try:
            self.global_toast.show(
                text, error=error, duration_ms=duration_ms
            )
            return
        except Exception:
            logger.exception("显示全局提示失败")
        if self.tray.notify(text, error=error):
            return
        if self._is_visible():
            try:
                self.toast.show(text, error=error, duration_ms=duration_ms)
            except tk.TclError:
                pass

    def _with_total_count(self, text: str) -> str:
        """在所有提示末尾附带当前录音总数。"""
        try:
            total = len(self.repository.snapshot())
        except Exception:
            logger.debug("读取录音总数失败", exc_info=True)
            return text
        return f"{text} · 共 {total} 条"

    def _show_from_tray(self, *, trigger_sync: bool = True) -> None:
        if self._closed:
            return
        try:
            self.deiconify()
            # 恢复用户上次的窗口状态；默认最大化。
            if self._want_maximized:
                self.state("zoomed")
            else:
                self.state("normal")
            # 短暂置顶，确保从全局快捷键唤起时窗口真正来到前台。
            self.attributes("-topmost", True)
            self.lift()
            self.focus_force()
            # 唤起后直接把焦点交给录音列表，方向键即可导航，无需先按 Tab。
            self.after_idle(self._focus_list)
            self.after(300, self._drop_topmost)
        except tk.TclError:
            pass
        if trigger_sync:
            # 用户可能刚编辑过 .env，重新读取配置并处理待上传项。
            self._apply_config_state()
            self._auto_enqueue()
            # 从系统托盘 / 全局快捷键唤醒也算“打开应用”，做一次云端同步。
            self._request_cloud_sync()

    def _drop_topmost(self) -> None:
        try:
            self.attributes("-topmost", False)
        except tk.TclError:
            pass
        # 唤醒初期窗口可能还没真正获得焦点，此时补一次列表聚焦；
        # 用户已经自己点到其他控件时则不再抢焦点。
        try:
            focus = self.focus_get()
        except (tk.TclError, KeyError):
            focus = None
        if focus is None or focus is self:
            self._focus_list()

    def _show_for_dialog(self) -> None:
        if not self._is_visible():
            self._show_from_tray(trigger_sync=False)

    def _on_quit_hotkey(self, _event=None) -> str:
        self._quit_app()
        return "break"

    def _on_close(self) -> None:
        """Alt+F4 / 关闭按钮：隐藏到系统托盘而不是退出。"""
        if self._closed:
            return
        if not self.tray.available:
            logger.info("系统托盘不可用，关闭窗口即退出")
            self._quit_app()
            return
        try:
            self.withdraw()
        except tk.TclError:
            return
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self._notify("已最小化到系统托盘，仍在后台运行")

    def _quit_app(self) -> None:
        if self._closed:
            return
        if self.recorder.active:
            self._show_for_dialog()
            if not messagebox.askyesno(
                "关闭",
                "正在录音。关闭前会先保存这段录音，确定关闭吗？",
                parent=self,
            ):
                return
            self._finish_recording_on_exit()
        self._shutdown()

    def _restart_app(self) -> None:
        if self._closed:
            return
        if self.recorder.active:
            self._show_for_dialog()
            if not messagebox.askyesno(
                "重启",
                "正在录音。重启前会先保存这段录音，确定重启吗？",
                parent=self,
            ):
                return
            self._finish_recording_on_exit()
        self.restart_requested = True
        self._shutdown()

    def _shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.tray.set_recording(False)
        self._cancel_ticker()
        if self._drain_id is not None:
            try:
                self.after_cancel(self._drain_id)
            except tk.TclError:
                pass
            self._drain_id = None
        if self._state_watch_id is not None:
            try:
                self.after_cancel(self._state_watch_id)
            except tk.TclError:
                pass
            self._state_watch_id = None
        self.player.stop()
        self._stop_play_progress()
        self.uploader.stop()
        self.sync_manager.shutdown()
        self.stt.stop()
        self.hotkey.stop()
        if self.show_hotkey is not None:
            self.show_hotkey.stop()
        self.tray.stop()
        try:
            self.toast.hide()
            self.global_toast.hide()
        except (AttributeError, tk.TclError):
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _finish_recording_on_exit(self) -> None:
        created_at = self.recorder.started_at_ms
        data, samplerate, duration_ms = self.recorder.stop()
        if data is None or len(data) == 0 or duration_ms < MIN_DURATION_MS:
            return
        try:
            recording = _write_recording(data, samplerate, duration_ms, created_at)
        except Exception:
            logger.exception("退出时保存录音失败")
            return
        self.repository.add(recording)
        logger.info("退出前已保存录音：%s", recording.file_name)


def _write_recording(data, samplerate: int, duration_ms: int, created_at: int) -> Recording:
    ensure_dirs()
    recording_id = uuid.uuid4().hex
    base_name = f"rec-{formats.file_timestamp(created_at)}-{recording_id[:6]}"
    wav_path = RECORDINGS_DIR / f"{base_name}.wav"
    audio.write_wav(wav_path, data, samplerate)
    final_path = audio.encode_m4a(wav_path)
    return Recording(
        id=recording_id,
        file_name=final_path.name,
        created_at=created_at,
        duration_ms=duration_ms,
        size_bytes=final_path.stat().st_size,
    )


def _without_transcript(recording: Recording) -> Recording:
    """返回去掉识别字段的副本，用于判断是否只需局部刷新。"""
    return replace(
        recording,
        transcript=None,
        transcript_status=TranscriptStatus.NONE,
        transcript_error=None,
        transcript_model=None,
        transcript_updated_at=0,
    )


def _status_view(theme: Theme, status: UploadStatus) -> tuple[str, str]:
    if status == UploadStatus.UPLOADING:
        color = theme.status_uploading
    elif status == UploadStatus.UPLOADED:
        color = theme.status_uploaded
    elif status == UploadStatus.FAILED:
        color = theme.status_failed
    else:
        color = theme.status_local
    return STATUS_TEXT.get(status, "待上传"), color
