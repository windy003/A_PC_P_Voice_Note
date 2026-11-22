"""R2 配置窗口：读写项目文件夹中的 .env。"""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox

from .. import r2
from ..config import (
    DEFAULT_PREFIX,
    R2Config,
    read_config,
    save_config,
)
from ..paths import DATA_DIR, ENV_PATH
from ..util import open_path
from .theme import Theme, UI_FONT
from .widgets import LinkButton, PillButton, ScrollFrame

FIELD_KEYS = ("account", "bucket", "access", "secret", "public", "prefix")
FIELD_LABELS = {
    "account": "账户 ID",
    "bucket": "存储桶名称",
    "access": "Access Key ID",
    "secret": "Secret Access Key",
    "public": "公开访问域名",
    "prefix": "对象前缀",
}


class SettingsDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        theme: Theme,
        *,
        post,
        on_saved=None,
        on_closed=None,
    ):
        super().__init__(parent)
        self._theme = theme
        self._post = post
        self._on_saved = on_saved
        self._on_closed = on_closed
        self._closed = False
        self._busy = False
        self._secret_visible = False
        self.entries: dict[str, tk.Entry] = {}

        self.title("R2 设置")
        self.configure(bg=theme.surface)
        self.transient(parent)
        self.resizable(True, True)
        self.minsize(600, 560)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())

        self._build()
        self._load_values()
        self._center(parent)
        self.grab_set()
        self.after(120, lambda: self.entries["account"].focus_set())

    # ------------------------------------------------------------------ 界面
    def _build(self) -> None:
        theme = self._theme

        header = tk.Frame(self, bg=theme.surface)
        header.pack(fill="x", padx=24, pady=(18, 4))
        text_block = tk.Frame(header, bg=theme.surface)
        text_block.pack(side="left", fill="x", expand=True)
        tk.Label(
            text_block,
            text="R2 设置",
            font=(UI_FONT, 18, "bold"),
            fg=theme.on_surface,
            bg=theme.surface,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            text_block,
            text="配置保存在项目文件夹的 .env 中",
            font=(UI_FONT, 9),
            fg=theme.on_surface_variant,
            bg=theme.surface,
            anchor="w",
        ).pack(fill="x", pady=(2, 0))
        LinkButton(
            header,
            "关闭",
            self._close,
            theme=theme,
            font=(UI_FONT, 9),
        ).pack(side="right", anchor="n")

        self.scroll = ScrollFrame(self, theme)
        self.scroll.pack(fill="both", expand=True, padx=24, pady=(10, 0))
        body = self.scroll.inner

        tk.Label(
            body,
            text=(
                "在 Cloudflare R2 中创建一个 Bucket，并创建一个只允许访问该 Bucket "
                "的 API Token（建议权限：Object Read & Write）。下面标有 * 的项目为必填。"
            ),
            font=(UI_FONT, 9),
            fg=theme.on_surface_variant,
            bg=theme.surface,
            anchor="w",
            justify="left",
            wraplength=560,
        ).pack(fill="x", pady=(0, 12))

        self.entries["account"] = self._field(
            body,
            "account",
            "账户 ID（Account ID）*",
            "Cloudflare 控制台 R2 概览页右侧的 Account ID",
        )
        self.entries["bucket"] = self._field(
            body,
            "bucket",
            "存储桶名称（Bucket）*",
            "例如 my-recordings",
        )
        self.entries["access"] = self._field(
            body,
            "access",
            "Access Key ID *",
            "R2 API Token 生成的 Access Key ID",
        )
        self.entries["secret"] = self._field(
            body,
            "secret",
            "Secret Access Key *",
            "只保存在本机的 .env 文件中，请勿分享",
            secret=True,
        )
        self.entries["public"] = self._field(
            body,
            "public",
            "公开访问域名（可选）",
            "例如 https://pub-xxxxxxxx.r2.dev，用于列表里的“打开”链接",
        )
        self.entries["prefix"] = self._field(
            body,
            "prefix",
            "对象前缀（可选）",
            f"默认 {DEFAULT_PREFIX}；上传后的对象键为“前缀 + 文件名”",
        )

        note = tk.Frame(body, bg=theme.secondary_container)
        note.pack(fill="x", pady=(8, 0))
        tk.Label(
            note,
            text=(
                "安全提示：Access Key 和 Secret 会以明文保存在项目文件夹的 .env 中，"
                "请勿把 .env 提交到 Git 或发送给别人。建议使用仅限该 Bucket 的"
                "最小权限 Token；手机或电脑丢失时请及时吊销。"
            ),
            font=(UI_FONT, 8),
            fg=theme.on_secondary_container,
            bg=theme.secondary_container,
            anchor="w",
            justify="left",
            wraplength=540,
            padx=14,
            pady=10,
        ).pack(fill="x")

        tk.Label(
            body,
            text=(
                "测试连接会调用 ListObjects，需要 Token 具备读取权限。"
                "若只给了写入权限，可以跳过测试直接保存。"
            ),
            font=(UI_FONT, 8),
            fg=theme.on_surface_variant,
            bg=theme.surface,
            anchor="w",
            justify="left",
            wraplength=560,
        ).pack(fill="x", pady=(10, 0))

        paths = tk.Frame(body, bg=theme.secondary_container)
        paths.pack(fill="x", pady=(12, 16))
        inner = tk.Frame(paths, bg=theme.secondary_container)
        inner.pack(fill="x", padx=14, pady=10)
        self._path_row(
            inner,
            "配置文件",
            str(ENV_PATH),
            "编辑 .env",
            lambda: open_path(ENV_PATH),
        )
        tk.Frame(inner, bg=theme.secondary_container, height=8).pack(fill="x")
        self._path_row(
            inner,
            "录音保存位置",
            str(DATA_DIR),
            "打开文件夹",
            lambda: open_path(DATA_DIR),
        )

        separator = tk.Frame(self, bg=theme.outline, height=1)
        separator.pack(fill="x", padx=24, pady=(10, 0))

        footer = tk.Frame(self, bg=theme.surface)
        footer.pack(fill="x", padx=24, pady=(8, 18))

        self.result_label = tk.Label(
            footer,
            text="填写完成后可以先测试连接，再保存。",
            font=(UI_FONT, 8),
            fg=theme.on_surface_variant,
            bg=theme.surface,
            anchor="w",
            justify="left",
            wraplength=580,
        )
        self.result_label.pack(fill="x", pady=(0, 8))

        buttons = tk.Frame(footer, bg=theme.surface)
        buttons.pack(fill="x")

        LinkButton(
            buttons,
            "取消",
            self._close,
            theme=theme,
            font=(UI_FONT, 9),
        ).pack(side="left", pady=8)

        self.btn_save = PillButton(
            buttons,
            "保存",
            self._save,
            theme=theme,
            width=136,
            height=42,
            font=(UI_FONT, 10, "bold"),
        )
        self.btn_save.pack(side="right")

        self.btn_test = PillButton(
            buttons,
            "测试连接",
            self._start_test,
            theme=theme,
            width=136,
            height=42,
            fill=theme.card,
            hover_fill=theme.secondary_container,
            text_color=theme.primary,
            outline=theme.outline,
            font=(UI_FONT, 10),
        )
        self.btn_test.pack(side="right", padx=(0, 10))

    def _field(
        self,
        parent: tk.Misc,
        key: str,
        label: str,
        helper: str,
        *,
        secret: bool = False,
    ) -> tk.Entry:
        theme = self._theme
        wrap = tk.Frame(parent, bg=theme.surface)
        wrap.pack(fill="x", pady=(0, 11))

        head = tk.Frame(wrap, bg=theme.surface)
        head.pack(fill="x")
        tk.Label(
            head,
            text=label,
            font=(UI_FONT, 9, "bold"),
            fg=theme.on_surface,
            bg=theme.surface,
            anchor="w",
        ).pack(side="left")

        entry = tk.Entry(
            wrap,
            font=(UI_FONT, 10),
            bg=theme.field,
            fg=theme.on_surface,
            insertbackground=theme.on_surface,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=theme.outline,
            highlightcolor=theme.primary,
        )
        entry.pack(fill="x", ipady=6, pady=(4, 0))

        if secret:
            entry.configure(show="•")
            self._secret_entry = entry
            self._secret_link = LinkButton(
                head,
                "显示",
                self._toggle_secret,
                theme=theme,
                font=(UI_FONT, 8),
            )
            self._secret_link.pack(side="right")

        entry.bind("<KeyRelease>", lambda _event, k=key: self._entry_edited(k))

        tk.Label(
            wrap,
            text=helper,
            font=(UI_FONT, 8),
            fg=theme.on_surface_variant,
            bg=theme.surface,
            anchor="w",
            justify="left",
            wraplength=560,
        ).pack(fill="x", pady=(3, 0))
        return entry

    def _path_row(self, parent, title: str, path: str, action_text: str, action) -> None:
        theme = self._theme
        row = tk.Frame(parent, bg=theme.secondary_container)
        row.pack(fill="x")
        tk.Label(
            row,
            text=title,
            font=(UI_FONT, 8, "bold"),
            fg=theme.on_secondary_container,
            bg=theme.secondary_container,
        ).pack(side="left")
        LinkButton(
            row,
            action_text,
            action,
            theme=theme,
            fg=theme.on_secondary_container,
            hover_fg=theme.primary,
            bg=theme.secondary_container,
            font=(UI_FONT, 8),
        ).pack(side="right")
        tk.Label(
            parent,
            text=path,
            font=(UI_FONT, 8),
            fg=theme.on_secondary_container,
            bg=theme.secondary_container,
            anchor="w",
            justify="left",
            wraplength=540,
        ).pack(fill="x", pady=(1, 0))

    def _center(self, parent: tk.Misc) -> None:
        self.update_idletasks()
        width = 660
        height = min(780, self.winfo_screenheight() - 100)
        try:
            parent_x = parent.winfo_rootx()
            parent_y = parent.winfo_rooty()
            parent_w = parent.winfo_width()
            parent_h = parent.winfo_height()
        except tk.TclError:
            parent_x = parent_y = 0
            parent_w = self.winfo_screenwidth()
            parent_h = self.winfo_screenheight()
        x = max(0, parent_x + (parent_w - width) // 2)
        y = max(0, parent_y + (parent_h - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    # ------------------------------------------------------------------ 数据
    def _load_values(self) -> None:
        config = read_config()
        self.entries["account"].insert(0, config.account_id)
        self.entries["bucket"].insert(0, config.bucket)
        self.entries["access"].insert(0, config.access_key_id)
        self.entries["secret"].insert(0, config.secret_access_key)
        self.entries["public"].insert(0, config.public_base_url)
        self.entries["prefix"].insert(0, config.key_prefix or DEFAULT_PREFIX)

    def _toggle_secret(self) -> None:
        self._secret_visible = not self._secret_visible
        self._secret_entry.configure(show="" if self._secret_visible else "•")
        self._secret_link.set_text("隐藏" if self._secret_visible else "显示")

    def _entry_edited(self, key: str) -> None:
        entry = self.entries.get(key)
        if entry is not None:
            entry.configure(highlightbackground=self._theme.outline)
        current = str(self.result_label.cget("text"))
        if current.startswith("请检查") or current.startswith("保存失败"):
            self._set_result("", self._theme.on_surface_variant)

    def _collect(self) -> R2Config | None:
        values = {key: entry.get().strip() for key, entry in self.entries.items()}
        errors: dict[str, str] = {}

        if not values["account"]:
            errors["account"] = "必填"
        if not values["bucket"]:
            errors["bucket"] = "必填"
        if not values["access"]:
            errors["access"] = "必填"
        if not values["secret"]:
            errors["secret"] = "必填"
        public = values["public"]
        if public and not (
            public.startswith("http://") or public.startswith("https://")
        ):
            errors["public"] = "需要以 http:// 或 https:// 开头"

        for key, entry in self.entries.items():
            entry.configure(
                highlightbackground=(
                    self._theme.error if key in errors else self._theme.outline
                )
            )

        if errors:
            first = next(key for key in FIELD_KEYS if key in errors)
            summary = "；".join(
                f"{FIELD_LABELS[key]}：{message}"
                for key, message in errors.items()
            )
            self._set_result("请检查：" + summary, self._theme.error)
            self.entries[first].focus_set()
            return None

        return R2Config(
            account_id=values["account"],
            bucket=values["bucket"],
            access_key_id=values["access"],
            secret_access_key=values["secret"],
            public_base_url=values["public"],
            key_prefix=values["prefix"] or DEFAULT_PREFIX,
        )

    # ------------------------------------------------------------------ 操作
    def _set_result(self, text: str, color: str) -> None:
        self.result_label.configure(text=text, fg=color)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.btn_test.set_enabled(not busy)
        self.btn_save.set_enabled(not busy)
        self.btn_test.set_text("正在测试…" if busy else "测试连接")

    def _start_test(self) -> None:
        if self._busy:
            return
        config = self._collect()
        if config is None:
            return
        self._set_busy(True)
        self._set_result("正在测试连接…", self._theme.on_surface_variant)
        threading.Thread(
            target=self._test_worker,
            args=(config,),
            name="r2-test",
            daemon=True,
        ).start()

    def _test_worker(self, config: R2Config) -> None:
        try:
            r2.test_connection(config)
            result = (True, "连接成功，Bucket 可访问")
        except r2.R2Error as exc:
            result = (False, f"连接失败：HTTP {exc.code}：{exc.detail}")
        except r2.R2NetworkError as exc:
            result = (False, f"连接失败：网络错误：{exc}")
        except Exception as exc:
            result = (False, f"连接失败：{exc}")
        self._post(lambda: self._apply_test_result(result))

    def _apply_test_result(self, result: tuple[bool, str]) -> None:
        if self._closed:
            return
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        self._set_busy(False)
        ok, text = result
        self._set_result(
            text,
            self._theme.success if ok else self._theme.error,
        )

    def _save(self) -> None:
        if self._busy:
            return
        config = self._collect()
        if config is None:
            return
        try:
            save_config(config)
        except Exception as exc:
            self._set_result(f"保存失败：{exc}", self._theme.error)
            messagebox.showerror("保存失败", str(exc), parent=self)
            return
        self._close()
        if self._on_saved is not None:
            self._on_saved()

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        if self._on_closed is not None:
            self._on_closed()
