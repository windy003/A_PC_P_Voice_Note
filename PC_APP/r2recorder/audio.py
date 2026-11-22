"""录音、音频播放与 ffmpeg 编码。"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Callable

try:  # 音频库缺失时应用仍可启动，只禁用录音/播放。
    import numpy as np
except Exception:  # pragma: no cover - 环境相关
    np = None  # type: ignore[assignment]

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - 环境相关
    sd = None  # type: ignore[assignment]

AUDIO_AVAILABLE = sd is not None and np is not None
FFMPEG_PATH = shutil.which("ffmpeg")

_CREATE_NO_WINDOW = 0
if sys.platform == "win32":
    _CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class AudioError(Exception):
    pass


def ffmpeg_available() -> bool:
    return FFMPEG_PATH is not None


def recording_format_label() -> str:
    return "M4A（AAC）" if ffmpeg_available() else "WAV（PCM）"


class Recorder:
    """使用 sounddevice 采集单声道 16-bit PCM。"""

    def __init__(self) -> None:
        self._stream = None
        self._chunks: list = []
        self._lock = threading.Lock()
        self._start_mono = 0.0
        self._started_at_ms = 0
        self._samplerate = 44100
        self.active = False

    @property
    def started_at_ms(self) -> int:
        return self._started_at_ms

    @property
    def samplerate(self) -> int:
        return self._samplerate

    @property
    def elapsed_ms(self) -> int:
        if not self.active:
            return 0
        return max(0, int((time.monotonic() - self._start_mono) * 1000))

    def _callback(self, indata, frames, time_info, status) -> None:
        with self._lock:
            self._chunks.append(indata.copy())

    def start(self) -> None:
        if self.active:
            return
        if not AUDIO_AVAILABLE:
            raise AudioError(
                "缺少音频库 sounddevice / numpy。\n"
                "请在项目文件夹运行：pip install -r requirements.txt"
            )

        with self._lock:
            self._chunks = []

        candidates = [44100]
        try:
            default_rate = int(round(float(sd.query_devices(kind="input")["default_samplerate"])))
            if default_rate not in candidates:
                candidates.append(default_rate)
        except Exception:
            pass

        last_error: Exception | None = None
        for samplerate in candidates:
            stream = None
            try:
                stream = sd.InputStream(
                    samplerate=samplerate,
                    channels=1,
                    dtype="int16",
                    callback=self._callback,
                )
                stream.start()
            except Exception as exc:
                last_error = exc
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                continue

            self._stream = stream
            self._samplerate = samplerate
            self._start_mono = time.monotonic()
            self._started_at_ms = int(time.time() * 1000)
            self.active = True
            return

        raise AudioError(
            f"无法打开麦克风：{last_error}\n请检查系统麦克风权限或默认输入设备。"
        )

    def stop(self):
        """停止录音并返回 (int16 数组, 采样率, 时长毫秒)。"""
        if not self.active:
            return None, self._samplerate, 0
        self.active = False
        stream, self._stream = self._stream, None
        try:
            if stream is not None:
                stream.stop()
                stream.close()
        except Exception:
            pass

        duration_ms = max(0, int((time.monotonic() - self._start_mono) * 1000))
        with self._lock:
            chunks = self._chunks
            self._chunks = []

        if not chunks:
            return None, self._samplerate, duration_ms
        data = np.concatenate(chunks, axis=0)
        return data, self._samplerate, duration_ms


def write_wav(path: str | Path, data, samplerate: int) -> None:
    if np is None:
        raise AudioError("缺少 numpy，无法写入录音文件")
    array = np.asarray(data)
    if array.ndim > 1:
        array = array[:, 0]
    array = np.ascontiguousarray(array, dtype="<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(samplerate))
        handle.writeframes(array.tobytes())


def encode_m4a(wav_path: str | Path) -> Path:
    """借助 ffmpeg 把 WAV 转成 M4A；失败时保留 WAV。"""
    wav_path = Path(wav_path)
    if not ffmpeg_available():
        return wav_path
    output = wav_path.with_suffix(".m4a")
    try:
        subprocess.run(
            [
                FFMPEG_PATH,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(wav_path),
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ac",
                "1",
                "-ar",
                "44100",
                str(output),
            ],
            check=True,
            capture_output=True,
            timeout=600,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        return wav_path

    if output.exists() and output.stat().st_size > 0:
        try:
            wav_path.unlink()
        except OSError:
            pass
        return output
    return wav_path


def _read_wav(path: Path):
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        samplerate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if sample_width != 2:
        raise AudioError("暂不支持该 WAV 编码（仅支持 16-bit PCM）")
    data = np.frombuffer(frames, dtype="<i2")
    if channels > 1:
        data = data.reshape(-1, channels)
    return data.copy(), samplerate


def load_audio(path: str | Path, cache_dir: str | Path):
    if not AUDIO_AVAILABLE:
        raise AudioError("缺少音频库 sounddevice / numpy，无法播放")
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".wav":
        return _read_wav(path)

    if suffix in {".m4a", ".mp4", ".aac", ".mp3", ".flac", ".ogg"}:
        if not ffmpeg_available():
            raise AudioError("播放该格式需要 ffmpeg，请先安装并加入 PATH")
        cache = Path(cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        temp_wav = cache / f"play-{uuid.uuid4().hex}.wav"
        try:
            subprocess.run(
                [
                    FFMPEG_PATH,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(path),
                    "-vn",
                    "-acodec",
                    "pcm_s16le",
                    "-f",
                    "wav",
                    str(temp_wav),
                ],
                check=True,
                capture_output=True,
                timeout=600,
                creationflags=_CREATE_NO_WINDOW,
            )
            return _read_wav(temp_wav)
        except subprocess.CalledProcessError as exc:
            message = (exc.stderr or b"").decode("utf-8", "replace").strip()
            raise AudioError(f"ffmpeg 解码失败：{message or '未知错误'}") from None
        finally:
            try:
                temp_wav.unlink(missing_ok=True)
            except OSError:
                pass

    raise AudioError(f"不支持的音频格式：{suffix or '未知'}")


class _Playback:
    __slots__ = ("path", "stop_event")

    def __init__(self, path: str):
        self.path = path
        self.stop_event = threading.Event()


class Player:
    """同一时间只播放一条录音，通过 sounddevice 异步播放。"""

    def __init__(self, cache_dir: str | Path):
        self._cache_dir = Path(cache_dir)
        self._lock = threading.Lock()
        self._token: _Playback | None = None

    def play(
        self,
        path: str | Path,
        on_finished: Callable[[str | None], None],
    ) -> None:
        if not AUDIO_AVAILABLE:
            raise AudioError("缺少音频库 sounddevice / numpy，无法播放")
        self.stop()
        target = str(Path(path))
        token = _Playback(target)
        with self._lock:
            self._token = token

        def run() -> None:
            error: str | None = None
            try:
                if token.stop_event.is_set():
                    return
                samples, samplerate = load_audio(target, self._cache_dir)
                if token.stop_event.is_set():
                    return
                sd.play(samples, samplerate)
                sd.wait()
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
            finally:
                with self._lock:
                    if self._token is token:
                        self._token = None
                try:
                    on_finished(error)
                except Exception:
                    pass

        threading.Thread(target=run, name="r2-playback", daemon=True).start()

    def stop(self) -> None:
        with self._lock:
            token = self._token
            self._token = None
        if token is not None:
            token.stop_event.set()
        try:
            if AUDIO_AVAILABLE:
                sd.stop()
        except Exception:
            pass
