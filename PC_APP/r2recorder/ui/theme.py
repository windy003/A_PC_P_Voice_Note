"""配色与字体。"""

from __future__ import annotations

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


if sys.platform == "win32":
    UI_FONT = "Microsoft YaHei UI"
    MONO_FONT = "Consolas"
elif sys.platform == "darwin":
    UI_FONT = "PingFang SC"
    MONO_FONT = "Menlo"
else:
    UI_FONT = "Noto Sans CJK SC"
    MONO_FONT = "DejaVu Sans Mono"
