"""配色与字体。"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    name: str
    surface: str
    card: str
    field: str
    on_surface: str
    on_surface_variant: str
    outline: str
    primary: str
    primary_hover: str
    on_primary: str
    primary_container: str
    on_primary_container: str
    secondary_container: str
    on_secondary_container: str
    error: str
    error_container: str
    on_error_container: str
    record_red: str
    record_red_hover: str
    on_record: str
    success: str
    status_local: str
    status_uploading: str
    status_uploaded: str
    status_failed: str
    chip_text: str
    disabled: str
    disabled_text: str
    scroll_thumb: str


LIGHT = Theme(
    name="light",
    surface="#F7F9FC",
    card="#FFFFFF",
    field="#FFFFFF",
    on_surface="#191C1E",
    on_surface_variant="#41474D",
    outline="#C8D1D9",
    primary="#006493",
    primary_hover="#005178",
    on_primary="#FFFFFF",
    primary_container="#C9E6FF",
    on_primary_container="#001E2F",
    secondary_container="#D2E5F5",
    on_secondary_container="#0B1D29",
    error="#BA1A1A",
    error_container="#FFDAD6",
    on_error_container="#410002",
    record_red="#C62828",
    record_red_hover="#A61F1F",
    on_record="#FFFFFF",
    success="#2E7D32",
    status_local="#546E7A",
    status_uploading="#1565C0",
    status_uploaded="#2E7D32",
    status_failed="#C62828",
    chip_text="#FFFFFF",
    disabled="#B9C3CB",
    disabled_text="#FFFFFF",
    scroll_thumb="#B7C2CB",
)

DARK = Theme(
    name="dark",
    surface="#0F1417",
    card="#171D21",
    field="#12181B",
    on_surface="#E2E4E6",
    on_surface_variant="#A9B4BB",
    outline="#333D43",
    primary="#8DCDF4",
    primary_hover="#B3DEF9",
    on_primary="#00344E",
    primary_container="#004C71",
    on_primary_container="#C9E6FF",
    secondary_container="#24323C",
    on_secondary_container="#D2E5F5",
    error="#FFB4AB",
    error_container="#93000A",
    on_error_container="#FFDAD6",
    record_red="#D94848",
    record_red_hover="#B93A3A",
    on_record="#FFFFFF",
    success="#81C784",
    status_local="#90A4AE",
    status_uploading="#64B5F6",
    status_uploaded="#81C784",
    status_failed="#EF9A9A",
    chip_text="#0B1D29",
    disabled="#4A555C",
    disabled_text="#D0D6DA",
    scroll_thumb="#3D484F",
)


if sys.platform == "win32":
    UI_FONT = "Microsoft YaHei UI"
    MONO_FONT = "Consolas"
elif sys.platform == "darwin":
    UI_FONT = "PingFang SC"
    MONO_FONT = "Menlo"
else:
    UI_FONT = "Noto Sans CJK SC"
    MONO_FONT = "DejaVu Sans Mono"


def system_prefers_dark() -> bool:
    override = os.environ.get("R2_RECORDER_THEME", "").strip().lower()
    if override == "dark":
        return True
    if override == "light":
        return False
    if sys.platform == "win32":
        try:
            import winreg

            path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
        except Exception:
            pass
    return False


def get_theme() -> Theme:
    return DARK if system_prefers_dark() else LIGHT
