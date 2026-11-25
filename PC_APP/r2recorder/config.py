"""从项目文件夹的 .env 读取并保存阿里云 OSS 配置。"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .paths import ENV_EXAMPLE_PATH, ENV_PATH

DEFAULT_PREFIX = "recordings/"

KEY_ENDPOINT = "OSS_ENDPOINT"
KEY_REGION = "OSS_REGION"
KEY_BUCKET = "OSS_BUCKET"
KEY_ACCESS_KEY = "OSS_ACCESS_KEY_ID"
KEY_SECRET_KEY = "OSS_ACCESS_KEY_SECRET"
KEY_PUBLIC_URL = "OSS_PUBLIC_BASE_URL"
KEY_PREFIX = "OSS_KEY_PREFIX"

ENV_KEYS = (
    KEY_ENDPOINT,
    KEY_REGION,
    KEY_BUCKET,
    KEY_ACCESS_KEY,
    KEY_SECRET_KEY,
    KEY_PUBLIC_URL,
    KEY_PREFIX,
)

_ENV_TEMPLATE = """# 阿里云 OSS 配置示例。
# 直接编辑本文件保存后，重启应用即可生效。
# Endpoint 可在 OSS 控制台 Bucket 概览页查看，例如 oss-cn-hangzhou.aliyuncs.com
OSS_ENDPOINT=
# 可选：地域 ID，例如 cn-hangzhou；留空时根据 Endpoint 自动推断
OSS_REGION=
OSS_BUCKET=
OSS_ACCESS_KEY_ID=
OSS_ACCESS_KEY_SECRET=
# 可选：公开访问域名，例如 https://my-bucket.oss-cn-hangzhou.aliyuncs.com
OSS_PUBLIC_BASE_URL=
# 可选：对象前缀，默认 recordings/
OSS_KEY_PREFIX=recordings/

# ---- 语音识别（STT）----
# OpenAI 兼容接口，默认使用 Groq Whisper；留空 STT_API_KEY 则禁用。
STT_BASE_URL=https://api.groq.com/openai/v1
STT_API_KEY=
STT_MODEL=whisper-large-v3-turbo
# 中文填 zh；留空自动检测
STT_LANGUAGE=zh
# 保存录音后自动识别：true / false
STT_AUTO=false
"""


def _unescape_double_quoted(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt in ('"', "\\"):
                out.append(nxt)
            else:
                out.append(ch)
                out.append(nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def parse_env_text(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in text.lstrip("﻿").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            if value[0] == '"':
                value = _unescape_double_quoted(value[1:-1])
            else:
                value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        result[key] = value
    return result


def load_env(path: str | os.PathLike[str] = ENV_PATH) -> dict[str, str]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    return parse_env_text(text)


def ensure_env_file(path: str | os.PathLike[str] = ENV_PATH) -> None:
    """首次运行时根据 .env.example 生成 .env，方便直接编辑文件配置。"""
    target = Path(path)
    if target.exists():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if ENV_EXAMPLE_PATH.is_file():
            shutil.copyfile(ENV_EXAMPLE_PATH, target)
        else:
            target.write_text(_ENV_TEMPLATE, encoding="utf-8", newline="\n")
    except OSError:
        # 配置模板不可用时不影响程序启动，用户仍可在设置窗口里配置。
        pass


def _encode_env_value(value: str) -> str:
    value = str(value)
    if value == "":
        return ""
    if re.fullmatch(r"[A-Za-z0-9_./:@%+\-]*", value):
        return value
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "")
        .replace("\n", "\\n")
    )
    return f'"{escaped}"'


def update_env_file(
    path: str | os.PathLike[str],
    updates: dict[str, str],
    *,
    section_title: str = "阿里云 OSS 配置",
) -> None:
    """原地更新 .env 中的键，保留注释与其它行。"""
    target = Path(path)
    try:
        existing = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        existing = ""

    lines = existing.splitlines()
    seen: set[str] = set()
    output: list[str] = []
    pattern = re.compile(
        r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*)=(.*)$"
    )
    for line in lines:
        match = pattern.match(line)
        if match and match.group(2) in updates:
            key = match.group(2)
            output.append(f"{match.group(1)}{key}={_encode_env_value(updates[key])}")
            seen.add(key)
        else:
            output.append(line)

    missing = [key for key in updates if key not in seen]
    if missing:
        if output and output[-1].strip():
            output.append("")
        output.append(f"# ---- {section_title} ----")
        for key in missing:
            output.append(f"{key}={_encode_env_value(updates[key])}")

    text = "\n".join(output).rstrip("\n") + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".env.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_path, target)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class OSSConfig:
    endpoint: str
    bucket: str
    access_key_id: str
    access_key_secret: str
    region: str = ""
    public_base_url: str = ""
    key_prefix: str = DEFAULT_PREFIX

    @property
    def endpoint_host(self) -> str:
        """去掉协议与末尾斜杠后的 Endpoint 主机名，例如 oss-cn-hangzhou.aliyuncs.com。"""
        host = self.endpoint.strip()
        lowered = host.lower()
        for scheme in ("https://", "http://"):
            if lowered.startswith(scheme):
                host = host[len(scheme):]
                break
        return host.rstrip("/").split("/", 1)[0]

    @property
    def resolved_region(self) -> str:
        """V4 签名所需的地域 ID；留空时从标准 Endpoint 推断，例如 cn-hangzhou。"""
        explicit = self.region.strip()
        if explicit:
            return explicit
        for label in self.endpoint_host.lower().split("."):
            if not label.startswith("oss-"):
                continue
            region = label[len("oss-"):]
            if region.endswith("-internal"):
                region = region[: -len("-internal")]
            if not region or region.startswith("accelerate"):
                # 全球加速域名不包含地域信息，必须手动填写 Region。
                return ""
            return region
        return ""

    @property
    def request_host(self) -> str:
        """虚拟主机风格的请求域名：Bucket.Endpoint。

        如果用户粘贴的 Endpoint 已经带了 Bucket 前缀，则直接使用，避免重复。
        """
        host = self.endpoint_host
        if host.lower().startswith(f"{self.bucket.lower()}."):
            return host
        return f"{self.bucket}.{host}"

    @property
    def normalized_prefix(self) -> str:
        prefix = self.key_prefix.strip().replace("\\", "/")
        while prefix.startswith("/"):
            prefix = prefix[1:]
        if not prefix:
            return DEFAULT_PREFIX
        if not prefix.endswith("/"):
            prefix += "/"
        return prefix

    def object_key_for(self, file_name: str) -> str:
        return self.normalized_prefix + file_name

    def public_url_for(self, object_key: str) -> str | None:
        base = self.public_base_url.strip().rstrip("/")
        if not base:
            return None
        encoded = "/".join(quote(segment, safe="-_.~") for segment in object_key.split("/"))
        return f"{base}/{encoded}"

    @property
    def is_complete(self) -> bool:
        return all(
            value.strip()
            for value in (
                self.endpoint,
                self.resolved_region,
                self.bucket,
                self.access_key_id,
                self.access_key_secret,
            )
        )


def read_config(path: str | os.PathLike[str] = ENV_PATH) -> OSSConfig:
    values = load_env(path)
    return OSSConfig(
        endpoint=values.get(KEY_ENDPOINT, "").strip(),
        region=values.get(KEY_REGION, "").strip(),
        bucket=values.get(KEY_BUCKET, "").strip(),
        access_key_id=values.get(KEY_ACCESS_KEY, "").strip(),
        access_key_secret=values.get(KEY_SECRET_KEY, "").strip(),
        public_base_url=values.get(KEY_PUBLIC_URL, "").strip(),
        key_prefix=values.get(KEY_PREFIX, DEFAULT_PREFIX).strip() or DEFAULT_PREFIX,
    )


def save_config(
    config: OSSConfig,
    path: str | os.PathLike[str] = ENV_PATH,
) -> None:
    update_env_file(
        path,
        {
            KEY_ENDPOINT: config.endpoint.strip(),
            KEY_REGION: config.region.strip(),
            KEY_BUCKET: config.bucket.strip(),
            KEY_ACCESS_KEY: config.access_key_id.strip(),
            KEY_SECRET_KEY: config.access_key_secret.strip(),
            KEY_PUBLIC_URL: config.public_base_url.strip(),
            KEY_PREFIX: config.key_prefix.strip() or DEFAULT_PREFIX,
        },
    )
