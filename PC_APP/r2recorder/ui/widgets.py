"""自定义 Tkinter 控件：圆角按钮、状态标签、滚动区域等。"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from .theme import Theme, UI_FONT


def parent_bg(widget: tk.Misc) -> str:
    try:
        return str(widget.cget("bg"))
    except Exception:
        return "#FFFFFF"


def _fill_round_rect(
    canvas: tk.Canvas,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius: float,
    fill: str,
) -> None:
    if x1 <= x0 or y1 <= y0:
        return
    radius = max(0.0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    if radius <= 0:
        canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline="")
        return
    diameter = radius * 2
    canvas.create_rectangle(x0 + radius, y0, x1 - radius, y1, fill=fill, outline="")
    canvas.create_rectangle(x0, y0 + radius, x1, y1 - radius, fill=fill, outline="")
    canvas.create_oval(x0, y0, x0 + diameter, y0 + diameter, fill=fill, outline="")
    canvas.create_oval(x1 - diameter, y0, x1, y0 + diameter, fill=fill, outline="")
    canvas.create_oval(x0, y1 - diameter, x0 + diameter, y1, fill=fill, outline="")
    canvas.create_oval(x1 - diameter, y1 - diameter, x1, y1, fill=fill, outline="")


def draw_round_rect(
    canvas: tk.Canvas,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius: float,
    fill: str,
    outline: str | None = None,
    outline_width: int = 1,
) -> None:
    if outline:
        _fill_round_rect(canvas, x0, y0, x1, y1, radius, outline)
        _fill_round_rect(
            canvas,
            x0 + outline_width,
            y0 + outline_width,
            x1 - outline_width,
            y1 - outline_width,
            max(0.0, radius - outline_width),
            fill,
        )
    else:
        _fill_round_rect(canvas, x0, y0, x1, y1, radius, fill)


class PillButton(tk.Canvas):
    """带悬停效果的圆角按钮。"""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command=None,
        *,
        theme: Theme,
        width: int = 120,
        height: int = 40,
        fill: str | None = None,
        hover_fill: str | None = None,
        text_color: str | None = None,
        font: tuple | None = None,
        outline: str | None = None,
        radius: float | None = None,
        disabled_fill: str | None = None,
    ):
        super().__init__(
            master,
            width=width,
            height=height,
            bg=parent_bg(master),
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._theme = theme
        self._command = command
        self._text = text
        self._width = width
        self._height = height
        self._fill = fill or theme.primary
        self._hover_fill = hover_fill or self._fill
        self._text_color = text_color or theme.on_primary
        self._font = font or (UI_FONT, 10, "bold")
        self._outline = outline
        self._radius = radius if radius is not None else height / 2
        self._disabled_fill = disabled_fill or theme.disabled
        self._enabled = True
        self._hover = False
        self._pressed = False

        self._draw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _draw(self) -> None:
        self.delete("all")
        if not self._enabled:
            fill = self._disabled_fill
            text_color = self._theme.disabled_text
            outline = None
        else:
            fill = self._hover_fill if (self._hover or self._pressed) else self._fill
            text_color = self._text_color
            outline = self._outline
        draw_round_rect(
            self,
            0,
            0,
            self._width - 1,
            self._height - 1,
            self._radius,
            fill,
            outline=outline,
        )
        self.create_text(
            self._width / 2,
            self._height / 2,
            text=self._text,
            fill=text_color,
            font=self._font,
        )

    def _on_enter(self, _event) -> None:
        if not self._enabled:
            return
        self._hover = True
        self._draw()

    def _on_leave(self, _event) -> None:
        self._hover = False
        self._pressed = False
        self._draw()

    def _on_press(self, _event) -> None:
        if not self._enabled:
            return
        self._pressed = True
        self._draw()

    def _on_release(self, event) -> None:
        if not self._enabled or not self._pressed:
            return
        self._pressed = False
        self._draw()
        inside = 0 <= event.x <= self._width and 0 <= event.y <= self._height
        if inside and self._command is not None:
            self._command()

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._draw()

    def set_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self._hover = False
        self._pressed = False
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def set_colors(
        self,
        fill: str,
        hover_fill: str,
        text_color: str,
        outline: str | None = None,
    ) -> None:
        self._fill = fill
        self._hover_fill = hover_fill
        self._text_color = text_color
        self._outline = outline
        self._draw()


class LinkButton(tk.Label):
    """文字按钮，用于列表操作和次要操作。"""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command=None,
        *,
        theme: Theme,
        fg: str | None = None,
        hover_fg: str | None = None,
        bg: str | None = None,
        font: tuple | None = None,
        disabled_fg: str | None = None,
    ):
        self._theme = theme
        self._command = command
        self._fg = fg or theme.primary
        self._hover_fg = hover_fg or theme.primary_hover
        self._disabled_fg = disabled_fg or theme.disabled
        self._enabled = True
        super().__init__(
            master,
            text=text,
            fg=self._fg,
            bg=bg or parent_bg(master),
            font=font or (UI_FONT, 9),
            cursor="hand2",
            padx=4,
            pady=1,
        )
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _on_enter(self, _event) -> None:
        if self._enabled:
            self.configure(fg=self._hover_fg)

    def _on_leave(self, _event) -> None:
        if self._enabled:
            self.configure(fg=self._fg)

    def _on_click(self, _event) -> None:
        if self._enabled and self._command is not None:
            self._command()

    def set_text(self, text: str) -> None:
        if str(self.cget("text")) != text:
            self.configure(text=text)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.configure(
            fg=self._fg if self._enabled else self._disabled_fg,
            cursor="hand2" if self._enabled else "arrow",
        )


class StatusChip(tk.Canvas):
    HEIGHT = 22

    def __init__(self, master: tk.Misc, theme: Theme):
        super().__init__(
            master,
            width=40,
            height=self.HEIGHT,
            bg=parent_bg(master),
            highlightthickness=0,
            bd=0,
        )
        self._theme = theme
        self._font = tkfont.Font(family=UI_FONT, size=8, weight="bold")
        self._text = ""
        self._color = theme.status_local

    def set(self, text: str, color: str) -> None:
        self._text = text
        self._color = color
        width = self._font.measure(text) + 20
        self.configure(width=width)
        self.delete("all")
        draw_round_rect(
            self,
            0,
            0,
            width - 1,
            self.HEIGHT - 1,
            self.HEIGHT / 2,
            color,
        )
        self.create_text(
            width / 2,
            self.HEIGHT / 2,
            text=text,
            fill=self._theme.chip_text,
            font=self._font,
        )


class ScrollFrame(tk.Frame):
    """Canvas + 内部 Frame 组成的可滚动区域。"""

    def __init__(self, master: tk.Misc, theme: Theme, bg: str | None = None):
        background = bg or theme.surface
        super().__init__(master, bg=background)
        self.theme = theme
        self._bg = background

        self.canvas = tk.Canvas(
            self,
            bg=background,
            highlightthickness=0,
            bd=0,
        )
        self.inner = tk.Frame(self.canvas, bg=background)
        self._window = self.canvas.create_window(
            (0, 0),
            window=self.inner,
            anchor="nw",
        )

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "R2.Vertical.TScrollbar",
            gripcount=0,
            background=theme.scroll_thumb,
            darkcolor=background,
            lightcolor=background,
            troughcolor=background,
            bordercolor=background,
            arrowcolor=theme.on_surface_variant,
            relief="flat",
            arrowsize=10,
            width=10,
        )
        style.map(
            "R2.Vertical.TScrollbar",
            background=[("active", theme.primary)],
        )

        self.vsb = ttk.Scrollbar(
            self,
            orient="vertical",
            command=self.canvas.yview,
            style="R2.Vertical.TScrollbar",
        )
        self.canvas.configure(yscrollcommand=self._on_scroll)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.vsb.grid_remove()

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        try:
            self.winfo_toplevel().bind("<MouseWheel>", self._on_mousewheel, add="+")
        except Exception:
            pass

    def _on_scroll(self, first: str, last: str) -> None:
        self.vsb.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.vsb.grid_remove()
        else:
            self.vsb.grid()

    def _on_inner_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._sync_scrollbar()

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)
        self._sync_scrollbar()

    def _sync_scrollbar(self) -> None:
        try:
            bbox = self.canvas.bbox("all")
            if bbox is None:
                return
            content_height = bbox[3] - bbox[1]
            visible = content_height > self.canvas.winfo_height() + 1
            if visible:
                self.vsb.grid()
            else:
                self.vsb.grid_remove()
        except tk.TclError:
            pass

    def _on_mousewheel(self, event) -> str | None:
        try:
            pointer_x, pointer_y = self.winfo_pointerxy()
            widget = self.winfo_containing(pointer_x, pointer_y)
        except (tk.TclError, KeyError):
            return None
        while widget is not None and widget is not self.canvas and widget is not self.inner:
            widget = widget.master
        if widget is None:
            return None

        bbox = self.canvas.bbox("all")
        if bbox is None or bbox[3] - bbox[1] <= self.canvas.winfo_height():
            return None

        delta = -3 if event.delta > 0 else 3
        self.canvas.yview_scroll(delta, "units")
        return "break"

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0.0)

    def view_position(self) -> float:
        try:
            return float(self.canvas.yview()[0])
        except tk.TclError:
            return 0.0
