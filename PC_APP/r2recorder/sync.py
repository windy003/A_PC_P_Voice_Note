"""R2 云端与本地录音的增量同步。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import audio, r2
from .config import R2Config
from .model import Recording, RecordingRepository, UploadStatus
from .paths import DATA_DIR, RECORDINGS_DIR

logger = logging.getLogger(__name__)

PENDING_DELETES_PATH = DATA_DIR / "pending_deletes.json"
AUDIO_SUFFIXES = {
    ".m4a",
    ".mp4",
    ".aac",
    ".wav",
    ".mp3",
    ".flac",
    ".ogg",
}
_CREATED_AT_RE = re.compile(r"rec-(\d{8})-(\d{6})")
_INVALID_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


@dataclass
class SyncResult:
    configured: bool = True
    connected: bool = False
    error: str | None = None
    downloaded: int = 0
    deleted_local: int = 0
    download_failed: int = 0
    pending_failed: int = 0


def format_sync_result(result: SyncResult) -> tuple[str, bool]:
    if not result.configured:
        return "云端同步未执行：尚未配置 R2", False
    if not result.connected:
        return f"云端连接失败：{result.error or '未知错误'}", True
    text = (
        "云端连接成功 · 下行同步完成："
        f"新增 {result.downloaded} 条，删除 {result.deleted_local} 条"
    )
    if result.download_failed:
        text += f"；{result.download_failed} 条下载失败"
    if result.pending_failed:
        text += f"；{result.pending_failed} 条云端删除待重试"
    return text, False


class PendingDeletes:
    """本机已删除、但云端还没删掉的 object key。"""

    def __init__(self, path: str | os.PathLike[str] = PENDING_DELETES_PATH):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._keys = self._load()

    def snapshot(self) -> set[str]:
        with self._lock:
            return set(self._keys)

    def add(self, key: str) -> None:
        key = str(key).strip()
        if not key:
            return
        with self._lock:
            if key in self._keys:
                return
            self._keys.add(key)
            self._persist_locked()

    def remove(self, key: str) -> None:
        with self._lock:
            if key not in self._keys:
                return
            self._keys.discard(key)
            self._persist_locked()

    def _load(self) -> set[str]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return set()
        if isinstance(raw, dict):
            raw = raw.get("keys", [])
        if not isinstance(raw, list):
            return set()
        return {str(item) for item in raw if str(item).strip()}

    def _persist_locked(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(
                prefix=".pending.", dir=str(self._path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(
                        sorted(self._keys),
                        handle,
                        ensure_ascii=False,
                        indent=2,
                    )
                    handle.write("\n")
                os.replace(temp_path, self._path)
            finally:
                try:
                    os.unlink(temp_path)
                except FileNotFoundError:
                    pass
        except OSError:
            logger.exception("保存待删除列表失败")


class SyncManager:
    """在后台线程里执行云端同步与云端删除，结果通过 post 回到界面线程。"""

    def __init__(
        self,
        repository: RecordingRepository,
        config_provider: Callable[[], R2Config | None],
        post: Callable[[Callable[[], None]], None],
        on_message: Callable[[str, bool, bool], None] | None = None,
    ):
        self._repository = repository
        self._config_provider = config_provider
        self._post = post
        self._on_message = on_message
        self._pending = PendingDeletes()
        self._network_lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._running = False
        self._closed = False

    # ------------------------------------------------------------------ 对外
    def request_sync(self, *, notify: bool = True) -> None:
        if self._closed:
            return
        with self._run_lock:
            if self._running:
                return
            self._running = True
        threading.Thread(
            target=self._sync_worker,
            args=(notify,),
            name="r2-sync",
            daemon=True,
        ).start()

    def delete_remote(
        self,
        object_key: str,
        label: str,
    ) -> None:
        if self._closed:
            return
        self._pending.add(object_key)
        threading.Thread(
            target=self._delete_worker,
            args=(object_key, label),
            name="r2-delete",
            daemon=True,
        ).start()

    def shutdown(self) -> None:
        self._closed = True

    # ------------------------------------------------------------------ 线程
    def _sync_worker(self, notify: bool) -> None:
        try:
            result = self._sync()
        except Exception as exc:
            logger.exception("云端同步失败")
            configured = True
            try:
                configured = self._config_provider() is not None
            except Exception:
                pass
            result = SyncResult(
                configured=configured,
                connected=False,
                error=str(exc) or exc.__class__.__name__,
            )
        finally:
            with self._run_lock:
                self._running = False
        if notify:
            text, error = format_sync_result(result)
            self._post_message(text, error)

    def _delete_worker(
        self,
        object_key: str,
        label: str,
    ) -> None:
        config = self._config_provider()
        if config is None:
            self._post_message(
                self._delete_result_text("未执行（未配置 R2）", None),
                True,
                include_total=False,
            )
            return
        with self._network_lock:
            try:
                r2.delete_object(config, object_key)
                deleted = True
            except Exception:
                logger.warning("云端删除失败：%s", object_key, exc_info=True)
                deleted = False
            cloud_count = self._count_remote_audio(config)
        if deleted:
            self._pending.remove(object_key)
            logger.info("云端删除成功：%s", label)
            self._post_message(
                self._delete_result_text("成功", cloud_count),
                False,
                include_total=False,
            )
        else:
            self._post_message(
                self._delete_result_text("失败，将在下次同步重试", cloud_count),
                True,
                include_total=False,
            )

    def _count_remote_audio(self, config: R2Config) -> int | None:
        try:
            objects = r2.list_objects(config, config.normalized_prefix)
        except Exception:
            logger.debug("统计云端录音数量失败", exc_info=True)
            return None
        return sum(1 for obj in objects if _is_audio_key(obj.key))

    def _delete_result_text(self, result: str, cloud_count: int | None) -> str:
        local_count = len(self._repository.snapshot())
        if cloud_count is None:
            return f"云端删除{result} · 本地 {local_count} 条，云端数量未知"
        return f"云端删除{result} · 本地 {local_count} 条，云端 {cloud_count} 条"

    def _post_message(
        self,
        text: str,
        error: bool,
        *,
        include_total: bool = True,
    ) -> None:
        if self._on_message is None:
            return
        self._post(lambda: self._on_message(text, error, include_total))

    # ------------------------------------------------------------------ 同步
    def _sync(self) -> SyncResult:
        config = self._config_provider()
        if config is None:
            return SyncResult(configured=False)

        result = SyncResult(configured=True, connected=False)
        with self._network_lock:
            # 1. 先把本机待删除的对象从云端删掉。
            for key in list(self._pending.snapshot()):
                try:
                    r2.delete_object(config, key)
                except Exception:
                    result.pending_failed += 1
                    logger.warning("删除云端对象失败：%s", key, exc_info=True)
                    continue
                self._pending.remove(key)

            # 2. 在拉取列表之前记录本地状态，避免同步中途上传完成的文件被误删。
            local_items = self._repository.snapshot()
            all_local_keys: set[str] = set()
            uploaded: dict[str, Recording] = {}
            for recording in local_items:
                key = recording.object_key or config.object_key_for(
                    recording.file_name
                )
                all_local_keys.add(key)
                if recording.status == UploadStatus.UPLOADED:
                    uploaded[key] = recording

            # 3. 拉云端列表；列表失败时不做任何本地删除。
            try:
                objects = r2.list_objects(config, config.normalized_prefix)
            except Exception as exc:
                result.error = str(exc) or exc.__class__.__name__
                return result
            result.connected = True
            remote = {
                obj.key: obj for obj in objects if _is_audio_key(obj.key)
            }

            # 4. 老版本记录没有 bucket/prefix 信息，确认云端存在后补上。
            for key, recording in uploaded.items():
                if (
                    recording.remote_bucket is None
                    and recording.remote_prefix is None
                    and key in remote
                ):
                    self._repository.update(
                        recording.id,
                        lambda item: replace(
                            item,
                            remote_bucket=config.bucket,
                            remote_prefix=config.normalized_prefix,
                        ),
                    )

            # 5. 下载云端有、本地没有的录音。
            pending = self._pending.snapshot()
            for key, remote_object in remote.items():
                if key in all_local_keys or key in pending:
                    continue
                try:
                    self._download_one(config, key, remote_object)
                except Exception:
                    logger.warning(
                        "下载云端录音失败：%s", key, exc_info=True
                    )
                    result.download_failed += 1
                    continue
                all_local_keys.add(key)
                result.downloaded += 1

            # 6. 云端没有、本地已上传的录音：确认 404 后删除本地。
            pending = self._pending.snapshot()
            for key, recording in uploaded.items():
                if key in remote or key in pending:
                    continue
                if (
                    recording.remote_bucket != config.bucket
                    or recording.remote_prefix != config.normalized_prefix
                ):
                    # 这条录音属于另一套 R2 配置，不能因为当前前缀看不到就删本地。
                    continue
                try:
                    if r2.object_exists(config, key):
                        continue
                except Exception:
                    logger.debug(
                        "确认对象是否存在失败：%s", key, exc_info=True
                    )
                    continue
                try:
                    recording.local_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "删除本地录音文件失败：%s",
                        recording.local_path,
                        exc_info=True,
                    )
                self._repository.delete(recording.id)
                result.deleted_local += 1
        return result

    def _download_one(
        self,
        config: R2Config,
        key: str,
        remote_object: r2.RemoteObject,
    ) -> Recording:
        desired = key.rsplit("/", 1)[-1] or "recording"
        desired = _safe_file_name(desired)
        file_name = self._unique_file_name(desired, key)
        target = RECORDINGS_DIR / file_name
        r2.download(config, key, target)

        created_at = (
            _created_at_from_key(key)
            or remote_object.last_modified_ms
            or int(time.time() * 1000)
        )
        record = Recording(
            id="sync-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
            file_name=file_name,
            created_at=created_at,
            duration_ms=audio.probe_duration_ms(target),
            size_bytes=target.stat().st_size,
            status=UploadStatus.UPLOADED,
            object_key=key,
            remote_url=config.public_url_for(key),
            remote_bucket=config.bucket,
            remote_prefix=config.normalized_prefix,
        )
        if self._repository.get(record.id) is not None:
            record = replace(record, id=uuid.uuid4().hex)
        self._repository.add(record)
        return record

    def _unique_file_name(self, desired: str, key: str) -> str:
        names = {item.file_name for item in self._repository.snapshot()}
        if desired not in names and not (RECORDINGS_DIR / desired).exists():
            return desired
        stem, suffix = os.path.splitext(desired)
        token = hashlib.sha256(key.encode("utf-8")).hexdigest()[:6]
        candidate = f"{stem}-{token}{suffix}"
        index = 2
        while candidate in names or (RECORDINGS_DIR / candidate).exists():
            candidate = f"{stem}-{token}-{index}{suffix}"
            index += 1
        return candidate


def _is_audio_key(key: str) -> bool:
    return Path(key).suffix.lower() in AUDIO_SUFFIXES


def _safe_file_name(name: str) -> str:
    cleaned = _INVALID_NAME_RE.sub("_", name).strip().rstrip(".")
    return cleaned or "recording"


def _created_at_from_key(key: str) -> int | None:
    match = _CREATED_AT_RE.search(key.rsplit("/", 1)[-1])
    if not match:
        return None
    try:
        parsed = datetime.strptime(
            match.group(1) + match.group(2), "%Y%m%d%H%M%S"
        )
    except ValueError:
        return None
    return int(parsed.timestamp() * 1000)
