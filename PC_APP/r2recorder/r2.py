"""Cloudflare R2 的最小 S3 客户端（AWS Signature V4，区域 auto，路径风格）。"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import quote

from .config import R2Config

ALGORITHM = "AWS4-HMAC-SHA256"
REGION = "auto"
SERVICE = "s3"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
TIMEOUT_SECONDS = 300

_CONTENT_TYPES = {
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
}


class R2Error(Exception):
    def __init__(self, code: int, detail: str):
        super().__init__(f"HTTP {code}: {detail}")
        self.code = code
        self.detail = detail

    @property
    def retryable(self) -> bool:
        return self.code in (408, 429) or self.code >= 500


class R2NetworkError(Exception):
    """连接失败、超时等网络层错误。"""


def content_type_for(file_name: str) -> str:
    return _CONTENT_TYPES.get(Path(file_name).suffix.lower(), "application/octet-stream")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _encode_segment(value: str) -> str:
    return quote(value, safe="-_.~")


def _canonical_query(params: dict[str, str]) -> str:
    pairs = sorted(params.items())
    return "&".join(f"{_encode_segment(str(k))}={_encode_segment(str(v))}" for k, v in pairs)


def _build_target(config: R2Config, object_key: str | None) -> tuple[str, str]:
    host = config.endpoint_host
    path = "/" + _encode_segment(config.bucket)
    if object_key:
        for segment in object_key.split("/"):
            if segment:
                path += "/" + _encode_segment(segment)
    return host, path


def _sign(
    config: R2Config,
    method: str,
    canonical_uri: str,
    query: dict[str, str],
    payload_hash: str,
    content_type: str | None,
) -> tuple[str, str, str]:
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    headers = {
        "host": config.endpoint_host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if content_type:
        headers["content-type"] = content_type

    canonical_headers = "".join(
        f"{name}:{headers[name].strip()}\n" for name in sorted(headers)
    )
    signed_headers = ";".join(sorted(headers))
    canonical_query = _canonical_query(query)
    canonical_request = "\n".join(
        [
            method,
            canonical_uri,
            canonical_query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )

    credential_scope = f"{date_stamp}/{REGION}/{SERVICE}/aws4_request"
    string_to_sign = "\n".join(
        [
            ALGORITHM,
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    signing_key = hmac.new(
        f"AWS4{config.secret_access_key}".encode("utf-8"),
        date_stamp.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    for part in (REGION, SERVICE, "aws4_request"):
        signing_key = hmac.new(
            signing_key,
            part.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    signature = hmac.new(
        signing_key,
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    authorization = (
        f"{ALGORITHM} Credential={config.access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return amz_date, authorization, canonical_query


def _extract_error(body: bytes, fallback: str) -> str:
    text = body.decode("utf-8", "replace").strip() if body else ""
    code_match = re.search(r"<Code>(.*?)</Code>", text, re.S)
    message_match = re.search(r"<Message>(.*?)</Message>", text, re.S)
    parts = []
    if code_match:
        parts.append(unescape(code_match.group(1).strip()))
    if message_match:
        parts.append(unescape(message_match.group(1).strip()))
    detail = " - ".join(part for part in parts if part)
    if not detail:
        detail = fallback or text
    detail = re.sub(r"\s+", " ", detail).strip()
    return detail[:400] or "未知错误"


def _request(
    config: R2Config,
    method: str,
    *,
    object_key: str | None = None,
    query: dict[str, str] | None = None,
    body=None,
    payload_hash: str | None = None,
    content_type: str | None = None,
    content_length: int | None = None,
) -> bytes:
    if not config.is_complete:
        raise ValueError("R2 配置不完整")

    host, canonical_uri = _build_target(config, object_key)
    query = query or {}
    if payload_hash is None:
        payload_hash = EMPTY_SHA256

    amz_date, authorization, canonical_query = _sign(
        config,
        method,
        canonical_uri,
        query,
        payload_hash,
        content_type,
    )

    path = canonical_uri
    if canonical_query:
        path += "?" + canonical_query

    headers = {
        "Host": host,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
        "Authorization": authorization,
    }
    if content_type:
        headers["Content-Type"] = content_type
    if content_length is not None:
        headers["Content-Length"] = str(content_length)

    connection = http.client.HTTPSConnection(host, timeout=TIMEOUT_SECONDS)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        if response.status >= 300:
            raise R2Error(response.status, _extract_error(data, response.reason))
        return data
    except R2Error:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise R2NetworkError(str(exc) or exc.__class__.__name__) from None
    finally:
        connection.close()


def upload(config: R2Config, file_path: str | Path, object_key: str) -> None:
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"本地文件不存在：{path}")
    payload_hash = sha256_file(path)
    size = path.stat().st_size
    with path.open("rb") as handle:
        _request(
            config,
            "PUT",
            object_key=object_key,
            body=handle,
            payload_hash=payload_hash,
            content_type=content_type_for(path.name),
            content_length=size,
        )


def test_connection(config: R2Config) -> None:
    _request(
        config,
        "GET",
        query={"list-type": "2", "max-keys": "1"},
    )
