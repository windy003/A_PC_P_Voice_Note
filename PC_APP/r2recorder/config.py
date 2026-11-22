"""从项目文件夹的 .env 读取并保存 Cloudflare R2 配置。"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .paths import ENV_PATH

DEFAULT_PREFIX = "recordings/"

KEY_ACCOUNT_ID = "R2_ACCOUNT_ID"
KEY_BUCKET = "R2_BUCKET"
KEY_ACCESS_KEY = "R2_ACCESS_KEY_ID"
KEY_SECRET_KEY = "R2_SECRET_ACCESS_KEY"
KEY_PUBLIC_URL = "R2_PUBLIC_BASE_URL"
KEY_PREFIX = "R2_KEY_PREFIX"

ENV_KEYS = (
    KEY_ACCOUNT_ID,
    KEY_BUCKET,
    KEY_ACCESS_KEY,
    KEY_SECRET_KEY,
    KEY_PUBLIC_URL,
    KEY_PREFIX,
)


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
        output.append("# ---- R2 配置 ----")
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
class R2Config:
    account_id: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    public_base_url: str = ""
    key_prefix: str = DEFAULT_PREFIX

    @property
    def endpoint_host(self) -> str:
        return f"{self.account_id}.r2.cloudflarestorage.com"

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
                self.account_id,
                self.bucket,
                self.access_key_id,
                self.secret_access_key,
            )
        )


def read_config(path: str | os.PathLike[str] = ENV_PATH) -> R2Config:
    values = load_env(path)
    return R2Config(
        account_id=values.get(KEY_ACCOUNT_ID, "").strip(),
        bucket=values.get(KEY_BUCKET, "").strip(),
        access_key_id=values.get(KEY_ACCESS_KEY, "").strip(),
        secret_access_key=values.get(KEY_SECRET_KEY, "").strip(),
        public_base_url=values.get(KEY_PUBLIC_URL, "").strip(),
        key_prefix=values.get(KEY_PREFIX, DEFAULT_PREFIX).strip() or DEFAULT_PREFIX,
    )


def save_config(
    config: R2Config,
    path: str | os.PathLike[str] = ENV_PATH,
) -> None:
    update_env_file(
        path,
        {
            KEY_ACCOUNT_ID: config.account_id.strip(),
            KEY_BUCKET: config.bucket.strip(),
            KEY_ACCESS_KEY: config.access_key_id.strip(),
            KEY_SECRET_KEY: config.secret_access_key.strip(),
            KEY_PUBLIC_URL: config.public_base_url.strip(),
            KEY_PREFIX: config.key_prefix.strip() or DEFAULT_PREFIX,
        },
    )
