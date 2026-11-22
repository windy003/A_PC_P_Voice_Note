"""后台上传队列：把本地录音上传到 R2，并维护状态。"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import replace
from typing import Callable

from . import r2
from .config import R2Config
from .model import RecordingRepository, UploadStatus

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAYS = (5, 10)


class UploadManager:
    def __init__(
        self,
        repository: RecordingRepository,
        config_provider: Callable[[], R2Config | None],
    ):
        self._repository = repository
        self._config_provider = config_provider
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(
            target=self._worker,
            name="r2-uploader",
            daemon=True,
        )
        self._thread.start()

    # ------------------------------------------------------------------ 对外接口
    def enqueue(self, recording_id: str, force: bool = False) -> None:
        with self._lock:
            if self._closed or recording_id in self._pending:
                return
            recording = self._repository.get(recording_id)
            if recording is None:
                return
            if not force and recording.status == UploadStatus.UPLOADED:
                return
            self._pending.add(recording_id)
        self._queue.put(recording_id)

    def enqueue_pending_local(self) -> int:
        count = 0
        for recording in self._repository.snapshot():
            if (
                recording.status == UploadStatus.LOCAL
                and recording.local_path.exists()
            ):
                self.enqueue(recording.id)
                count += 1
        return count

    def recover_interrupted(self) -> int:
        """把上次异常退出时残留的 UPLOADING 状态恢复为待上传。"""
        count = 0
        for recording in self._repository.snapshot():
            if recording.status == UploadStatus.UPLOADING:
                self._repository.update(
                    recording.id,
                    lambda item: replace(
                        item,
                        status=UploadStatus.LOCAL,
                        error=None,
                    ),
                )
                count += 1
        return count

    def retry_all(self) -> int:
        targets = [
            item
            for item in self._repository.snapshot()
            if item.status in (UploadStatus.LOCAL, UploadStatus.FAILED)
            and item.local_path.exists()
        ]
        for item in targets:
            self._repository.update(
                item.id,
                lambda recording: replace(
                    recording,
                    status=UploadStatus.LOCAL,
                    error=None,
                ),
            )
            self.enqueue(item.id, force=True)
        return len(targets)

    def stop(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._queue.put(None)

    # ------------------------------------------------------------------ 后台线程
    def _worker(self) -> None:
        while True:
            recording_id = self._queue.get()
            if recording_id is None:
                self._queue.task_done()
                return
            try:
                self._process(recording_id)
            except Exception:
                logger.exception("上传任务异常（%s）", recording_id)
            finally:
                with self._lock:
                    self._pending.discard(recording_id)
                self._queue.task_done()

    def _process(self, recording_id: str) -> None:
        recording = self._repository.get(recording_id)
        if recording is None or recording.status == UploadStatus.UPLOADED:
            return

        config = self._config_provider()
        if config is None:
            self._fail(recording_id, "未配置 R2")
            return

        file_path = recording.local_path
        if not file_path.exists():
            self._fail(recording_id, "本地录音文件不存在")
            return

        object_key = config.object_key_for(recording.file_name)
        self._set(recording_id, UploadStatus.UPLOADING, error=None)

        last_error = "上传失败"
        for attempt in range(MAX_ATTEMPTS):
            try:
                r2.upload(config, file_path, object_key)
            except (r2.R2Error, r2.R2NetworkError) as exc:
                if isinstance(exc, r2.R2Error):
                    message = f"HTTP {exc.code}：{exc.detail}"
                    retryable = exc.retryable
                else:
                    message = f"网络错误：{exc}"
                    retryable = True
                last_error = message
                if retryable and attempt < MAX_ATTEMPTS - 1:
                    self._set(
                        recording_id,
                        UploadStatus.FAILED,
                        error=message + "；上传失败，将自动重试",
                    )
                    time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                    self._set(recording_id, UploadStatus.UPLOADING, error=None)
                    continue
                self._fail(recording_id, message)
                return
            except Exception as exc:
                logger.exception("上传录音失败（%s）", recording_id)
                self._fail(recording_id, str(exc) or "未知错误")
                return
            else:
                self._repository.update(
                    recording_id,
                    lambda item: replace(
                        item,
                        status=UploadStatus.UPLOADED,
                        object_key=object_key,
                        remote_url=config.public_url_for(object_key),
                        error=None,
                    ),
                )
                return

        self._fail(recording_id, last_error)

    def _set(
        self,
        recording_id: str,
        status: UploadStatus,
        **changes,
    ) -> None:
        self._repository.update(
            recording_id,
            lambda item: replace(item, status=status, **changes),
        )

    def _fail(self, recording_id: str, message: str) -> None:
        self._set(recording_id, UploadStatus.FAILED, error=message)
