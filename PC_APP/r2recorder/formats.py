"""界面与文件名使用的格式化函数。"""

from __future__ import annotations

import re
from datetime import datetime

_FILE_TIME_RE = re.compile(r"rec-(\d{8})-(\d{6})")


def duration(ms: int) -> str:
    total_seconds = max(0, int(ms) // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    kb = num_bytes / 1024.0
    if kb < 1024:
        return f"{kb:.1f} KB"
    return f"{kb / 1024.0:.1f} MB"


def date_time(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000.0).strftime("%Y-%m-%d %H:%M")


def display_name(timestamp_ms: int) -> str:
    stamp = datetime.fromtimestamp(timestamp_ms / 1000.0).strftime("%Y-%m-%d %H:%M:%S")
    return f"录音 {stamp}"


def file_timestamp(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000.0).strftime("%Y%m%d-%H%M%S")


def filename_time_ms(name: str) -> int | None:
    """从录音文件名（rec-YYYYMMDD-HHMMSS）解析时间，无法解析时返回 None。"""
    match = _FILE_TIME_RE.search(name)
    if match is None:
        return None
    try:
        stamp = datetime.strptime(
            match.group(1) + match.group(2), "%Y%m%d%H%M%S"
        )
    except ValueError:
        return None
    return int(stamp.timestamp() * 1000)
