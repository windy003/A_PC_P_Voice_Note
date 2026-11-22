"""录音记录模型与本地 JSON 仓库。"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Callable

from .paths import META_PATH, RECORDINGS_DIR

logger = logging.getLogger(__name__)


class UploadStatus(str, Enum):
    LOCAL = "local"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    FAILED = "failed"

    @classmethod
    def parse(cls, value: object) -> "UploadStatus":
        if isinstance(value, cls):
            return value
        text = str(value or "").strip().lower()
        for item in cls:
            if text == item.value or text == item.name.lower():
                return item
        return cls.LOCAL


@dataclass(frozen=True)
class Recording:
    id: str
    file_name: str
    created_at: int  # 毫秒时间戳
    duration_ms: int
    size_bytes: int
    status: UploadStatus = UploadStatus.LOCAL
    object_key: str | None = None
    remote_url: str | None = None
    error: str | None = None

    @property
    def local_path(self) -> Path:
        return RECORDINGS_DIR / self.file_name

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Recording | None":
        try:
            return cls(
                id=str(data["id"]),
                file_name=str(data["file_name"]),
                created_at=int(data.get("created_at", 0)),
                duration_ms=int(data.get("duration_ms", 0)),
                size_bytes=int(data.get("size_bytes", 0)),
                status=UploadStatus.parse(data.get("status")),
                object_key=_optional_str(data.get("object_key")),
                remote_url=_optional_str(data.get("remote_url")),
                error=_optional_str(data.get("error")),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


class RecordingRepository:
    """线程安全的录音元数据仓库，变更时通知监听者。"""

    def __init__(self, meta_path: str | os.PathLike[str] = META_PATH):
        self._meta_path = Path(meta_path)
        self._lock = threading.RLock()
        self._listeners: list[Callable[[list[Recording]], None]] = []
        self._items: list[Recording] = []
        self._load()

    def add_listener(self, callback: Callable[[list[Recording]], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def snapshot(self) -> list[Recording]:
        with self._lock:
            return list(self._items)

    def get(self, recording_id: str) -> Recording | None:
        with self._lock:
            for item in self._items:
                if item.id == recording_id:
                    return item
        return None

    def add(self, recording: Recording) -> None:
        with self._lock:
            self._items = sorted(
                [recording, *self._items],
                key=lambda item: item.created_at,
                reverse=True,
            )
            self._persist_locked()
            snapshot = list(self._items)
        self._notify(snapshot)

    def update(
        self,
        recording_id: str,
        transform: Callable[[Recording], Recording],
    ) -> Recording | None:
        changed_item: Recording | None = None
        with self._lock:
            next_items: list[Recording] = []
            changed = False
            for item in self._items:
                if item.id == recording_id:
                    next_item = transform(item)
                    if next_item != item:
                        changed = True
                    changed_item = next_item
                    next_items.append(next_item)
                else:
                    next_items.append(item)
            if changed:
                self._items = next_items
                self._persist_locked()
                snapshot = list(self._items)
            else:
                snapshot = []
        if changed:
            self._notify(snapshot)
        return changed_item

    def delete(self, recording_id: str) -> bool:
        with self._lock:
            next_items = [item for item in self._items if item.id != recording_id]
            if len(next_items) == len(self._items):
                return False
            self._items = next_items
            self._persist_locked()
            snapshot = list(self._items)
        self._notify(snapshot)
        return True

    def _notify(self, snapshot: list[Recording]) -> None:
        for callback in list(self._listeners):
            try:
                callback(snapshot)
            except Exception:
                logger.exception("录音仓库监听器执行失败")

    def _load(self) -> None:
        if not self._meta_path.exists():
            return
        try:
            raw = json.loads(self._meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            logger.exception("读取录音元数据失败")
            return
        items: list[Recording] = []
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict):
                    recording = Recording.from_dict(entry)
                    if recording is not None:
                        items.append(recording)
        items.sort(key=lambda item: item.created_at, reverse=True)
        self._items = items

    def _persist_locked(self) -> None:
        payload = [item.to_dict() for item in self._items]
        try:
            self._meta_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._meta_path.with_suffix(".json.tmp")
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp_path, self._meta_path)
        except OSError:
            logger.exception("保存录音元数据失败")


def update_status(recording: Recording, status: UploadStatus, **kwargs) -> Recording:
    """便捷的状态更新辅助函数。"""
    return replace(recording, status=status, **kwargs)
