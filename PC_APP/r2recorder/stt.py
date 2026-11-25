"""OpenAI 兼容的语音转文字（STT）客户端与后台队列。

默认使用 Groq 的 Whisper 接口；任何兼容
``POST {base_url}/audio/transcriptions`` 的服务（例如硅基流动）都可以通过
.env 中的 STT_BASE_URL / STT_MODEL 切换，而无需改代码。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from . import audio, formats
from .config import load_env, update_env_file
from .model import RecordingRepository, TranscriptStatus
from .paths import ENV_PATH

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "whisper-large-v3-turbo"
DEFAULT_LANGUAGE = "zh"

KEY_BASE_URL = "STT_BASE_URL"
KEY_API_KEY = "STT_API_KEY"
KEY_MODEL = "STT_MODEL"
KEY_LANGUAGE = "STT_LANGUAGE"
KEY_AUTO = "STT_AUTO"

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
TRANSCODE_THRESHOLD_BYTES = 20 * 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 180
MAX_ATTEMPTS = 3
RETRY_DELAYS = (3, 8)
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

CONTENT_TYPES = {
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
}

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class STTError(Exception):
    pass


@dataclass(frozen=True)
class STTConfig:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    language: str = DEFAULT_LANGUAGE
    auto: bool = False

    @property
    def is_complete(self) -> bool:
        return bool(
            self.base_url.strip()
            and self.api_key.strip()
            and self.model.strip()
        )

    @property
    def endpoint(self) -> str:
        base = self.base_url.strip().rstrip("/")
        if base.endswith("/audio/transcriptions"):
            return base
        return f"{base}/audio/transcriptions"


def _parse_bool(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def read_stt_config(path: str | os.PathLike[str] = ENV_PATH) -> STTConfig:
    values = load_env(path)
    language = (
        values[KEY_LANGUAGE].strip()
        if KEY_LANGUAGE in values
        else DEFAULT_LANGUAGE
    )
    return STTConfig(
        base_url=values.get(KEY_BASE_URL, "").strip() or DEFAULT_BASE_URL,
        api_key=values.get(KEY_API_KEY, "").strip(),
        model=values.get(KEY_MODEL, "").strip() or DEFAULT_MODEL,
        language=language,
        auto=_parse_bool(values.get(KEY_AUTO)),
    )


def save_stt_config(
    config: STTConfig,
    path: str | os.PathLike[str] = ENV_PATH,
) -> None:
    update_env_file(
        path,
        {
            KEY_BASE_URL: config.base_url.strip() or DEFAULT_BASE_URL,
            KEY_API_KEY: config.api_key.strip(),
            KEY_MODEL: config.model.strip() or DEFAULT_MODEL,
            KEY_LANGUAGE: config.language.strip(),
            KEY_AUTO: "true" if config.auto else "false",
        },
        section_title="语音识别（STT）配置",
    )


# ---------------------------------------------------------------------- 请求
def transcribe_file(
    path: str | Path,
    config: STTConfig,
) -> str:
    """把单个音频文件发送到 STT 接口并返回识别出的文字。"""
    if not config.is_complete:
        raise STTError("尚未配置语音识别 API Key")
    source = Path(path)
    if not source.is_file():
        raise STTError("本地录音文件不存在")

    upload_path, temporary = _prepare_upload(source)
    try:
        try:
            file_bytes = upload_path.read_bytes()
        except OSError as exc:
            raise STTError(f"读取录音文件失败：{exc}") from None
        fields = {
            "model": config.model,
            "response_format": "json",
            "temperature": "0",
        }
        if config.language.strip():
            fields["language"] = config.language.strip()
        return _request_transcription(
            config,
            upload_path.name,
            file_bytes,
            fields,
        )
    finally:
        if temporary:
            shutil.rmtree(upload_path.parent, ignore_errors=True)


def _prepare_upload(path: Path) -> tuple[Path, bool]:
    """返回 (待上传文件, 是否为临时文件)；必要时先转成小体积 mp3。"""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise STTError(f"读取录音文件失败：{exc}") from None
    suffix = path.suffix.lower()
    supported = suffix in CONTENT_TYPES
    if supported and size <= TRANSCODE_THRESHOLD_BYTES:
        return path, False

    if not audio.ffmpeg_available():
        if supported and size <= MAX_UPLOAD_BYTES:
            return path, False
        if supported:
            raise STTError(
                f"录音文件有 {formats.size(size)}，超过接口 25MB 限制；"
                "安装 ffmpeg 后会自动压缩再识别"
            )
        raise STTError(f"不支持 {suffix or '该'} 格式；安装 ffmpeg 后会自动转换")

    return _transcode(path), True


def _transcode(path: Path) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="stt-"))
    attempts = (
        (temp_dir / "audio.mp3", "libmp3lame"),
        (temp_dir / "audio.m4a", "aac"),
    )
    last_error = ""
    for output, codec in attempts:
        command = [
            audio.FFMPEG_PATH,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            codec,
            "-b:a",
            "64k",
            str(output),
        ]
        try:
            subprocess.run(
                command,
                check=True,
                capture_output=True,
                timeout=600,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as exc:
            stderr = b""
            if isinstance(exc, subprocess.CalledProcessError):
                stderr = exc.stderr or b""
            last_error = stderr.decode("utf-8", "replace").strip() or str(exc)
            continue
        if output.exists() and output.stat().st_size > 0:
            if output.stat().st_size > MAX_UPLOAD_BYTES:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise STTError("录音太长，压缩后仍超过接口 25MB 限制")
            return output
    shutil.rmtree(temp_dir, ignore_errors=True)
    raise STTError(f"音频转换失败：{last_error or '未知错误'}")


def _request_transcription(
    config: STTConfig,
    file_name: str,
    file_bytes: bytes,
    fields: dict[str, str],
) -> str:
    body, content_type = _encode_multipart(fields, "file", file_name, file_bytes)
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": content_type,
        "Accept": "application/json",
        "User-Agent": "OSS-Recorder-STT",
    }
    last_error = "识别失败"
    for attempt in range(MAX_ATTEMPTS):
        request = urllib.request.Request(
            config.endpoint,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT_SECONDS
            ) as response:
                raw = response.read()
            return _parse_response(raw)
        except urllib.error.HTTPError as exc:
            detail = _http_error_detail(exc)
            last_error = _http_error_message(exc.code, detail)
            if exc.code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS - 1:
                time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                continue
            raise STTError(last_error) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            last_error = f"无法连接识别服务：{reason}"
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])
                continue
            raise STTError(
                last_error + "（请检查网络与 STT_BASE_URL）"
            ) from None
    raise STTError(last_error)


def _encode_multipart(
    fields: dict[str, str],
    file_field: str,
    file_name: str,
    file_bytes: bytes,
) -> tuple[bytes, str]:
    boundary = "----VoiceNote" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        if not value:
            continue
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    safe_name = os.path.basename(file_name).replace('"', "'") or "audio"
    content_type = CONTENT_TYPES.get(
        Path(safe_name).suffix.lower(), "application/octet-stream"
    )
    chunks.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{safe_name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(file_bytes)
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _parse_response(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        raise STTError("识别服务返回了空响应")
    try:
        payload = json.loads(text)
    except ValueError:
        # 某些服务在 response_format=text 时直接返回纯文本。
        return text

    if isinstance(payload, dict):
        error = payload.get("error")
        if error:
            if isinstance(error, dict):
                message = str(error.get("message") or "").strip()
            else:
                message = str(error).strip()
            raise STTError(message or "识别服务返回错误")
        content = payload.get("text")
        if isinstance(content, str):
            content = content.strip()
            if not content:
                raise STTError("未识别到语音内容")
            return content
    raise STTError("识别服务返回格式不符合预期")


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read(4096)
    except Exception:
        raw = b""
    text = raw.decode("utf-8", "replace").strip()
    if text:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "").strip()
                if message:
                    return message[:300]
            elif isinstance(error, str) and error.strip():
                return error.strip()[:300]
            message = str(payload.get("message") or "").strip()
            if message:
                return message[:300]
        compact = " ".join(text.split())
        if compact:
            return compact[:300]
    return str(exc.reason or "").strip()[:300]


def _http_error_message(code: int, detail: str) -> str:
    if code == 401:
        return "API Key 无效或已过期，请检查 STT_API_KEY"
    if code == 403:
        return "语音识别服务拒绝了请求（403），请检查 API Key 权限"
    if code == 413:
        return "录音文件超过语音识别接口的大小限制"
    if code == 429:
        return "识别请求过于频繁（429），请稍后再试"
    if code >= 500:
        return f"识别服务暂时不可用（HTTP {code}），请稍后重试"
    if detail:
        return f"识别失败（HTTP {code}）：{detail}"
    return f"识别失败（HTTP {code}）"


# ---------------------------------------------------------------------- 队列
class STTManager:
    """串行处理识别任务，结果通过 repository 更新并回主线程提示。"""

    def __init__(
        self,
        repository: RecordingRepository,
        config_provider: Callable[[], STTConfig | None],
        post: Callable[[Callable[[], None]], None],
        on_message: Callable[[str, bool, str | None], None] | None = None,
    ):
        self._repository = repository
        self._config_provider = config_provider
        self._post = post
        self._on_message = on_message
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(
            target=self._worker,
            name="stt-worker",
            daemon=True,
        )
        self._thread.start()

    # ------------------------------------------------------------------ 对外
    def enqueue(self, recording_id: str, force: bool = False) -> bool:
        with self._lock:
            if self._closed or recording_id in self._pending:
                return False
            recording = self._repository.get(recording_id)
            if recording is None or not recording.local_path.exists():
                return False
            if (
                not force
                and recording.transcript
                and recording.transcript_status == TranscriptStatus.DONE
            ):
                return False
            self._pending.add(recording_id)
        self._set(
            recording_id,
            TranscriptStatus.PENDING,
            transcript_error=None,
        )
        self._queue.put(recording_id)
        return True

    def recover_interrupted(self) -> int:
        """把上次异常退出时残留的“识别中”状态恢复为可重试。"""
        count = 0
        for recording in self._repository.snapshot():
            if recording.transcript_status != TranscriptStatus.PENDING:
                continue
            self._repository.update(
                recording.id,
                lambda item: replace(
                    item,
                    transcript_status=TranscriptStatus.FAILED,
                    transcript_error="上次识别被中断，请重试",
                ),
            )
            count += 1
        return count

    def stop(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._queue.put(None)

    # ------------------------------------------------------------------ 线程
    def _worker(self) -> None:
        while True:
            recording_id = self._queue.get()
            if recording_id is None:
                self._queue.task_done()
                return
            try:
                self._process(recording_id)
            except Exception:
                logger.exception("语音识别任务异常（%s）", recording_id)
            finally:
                with self._lock:
                    self._pending.discard(recording_id)
                self._queue.task_done()

    def _process(self, recording_id: str) -> None:
        recording = self._repository.get(recording_id)
        if recording is None:
            return
        config = self._config_provider()
        if config is None:
            self._fail(recording_id, "未配置语音识别 API Key")
            return
        if not recording.local_path.exists():
            self._fail(recording_id, "本地录音文件不存在")
            return

        try:
            text = transcribe_file(recording.local_path, config)
        except STTError as exc:
            self._fail(recording_id, str(exc) or "识别失败")
            return
        except Exception as exc:
            logger.exception("语音识别失败（%s）", recording_id)
            self._fail(recording_id, str(exc) or "识别失败")
            return

        if self._repository.get(recording_id) is None:
            # 识别过程中录音被删除了，不再写回。
            return
        self._repository.update(
            recording_id,
            lambda item: replace(
                item,
                transcript=text,
                transcript_status=TranscriptStatus.DONE,
                transcript_error=None,
                transcript_model=config.model,
                transcript_updated_at=int(time.time() * 1000),
            ),
        )
        self._message(
            "语音识别完成："
            + formats.display_name(recording.created_at),
            False,
            recording_id,
        )

    def _set(
        self,
        recording_id: str,
        status: TranscriptStatus,
        **changes,
    ) -> None:
        self._repository.update(
            recording_id,
            lambda item: replace(item, transcript_status=status, **changes),
        )

    def _fail(self, recording_id: str, message: str) -> None:
        if self._repository.get(recording_id) is None:
            return
        self._set(
            recording_id,
            TranscriptStatus.FAILED,
            transcript_error=message,
        )
        self._message(f"语音识别失败：{message}", True, recording_id)

    def _message(
        self,
        text: str,
        error: bool,
        recording_id: str | None,
    ) -> None:
        if self._on_message is None:
            return
        self._post(
            lambda: self._on_message(text, error, recording_id)
        )
