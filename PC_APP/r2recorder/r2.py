"""Cloudflare R2 的最小 S3 客户端（AWS Signature V4，区域 auto，路径风格）。"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import os
import re
import tempfile
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
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
LIST_PAGE_SIZE = 1000

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


# ---------------------------------------------------------------------- 同步
@dataclass(frozen=True)
class RemoteObject:
    key: str
    size: int
    last_modified_ms: int


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_iso_ms(value: str) -> int:
    text = value.strip()
    if not text:
        return 0
    try:
        return int(
            datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000
        )
    except ValueError:
        return 0


def parse_list_response(data: bytes) -> tuple[list[RemoteObject], str | None]:
    """解析 ListObjectsV2 的 XML，返回 (对象列表, 下一页 token)。"""
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise R2Error(200, f"无法解析对象列表：{exc}") from None

    objects: list[RemoteObject] = []
    truncated = False
    token: str | None = None
    for node in root.iter():
        name = _local_name(node.tag)
        if name == "Contents":
            key = ""
            size = 0
            modified = 0
            for child in node:
                child_name = _local_name(child.tag)
                if child_name == "Key":
                    key = child.text or ""
                elif child_name == "Size":
                    try:
                        size = int(child.text or 0)
                    except ValueError:
                        size = 0
                elif child_name == "LastModified":
                    modified = _parse_iso_ms(child.text or "")
            if key:
                objects.append(RemoteObject(key, size, modified))
        elif name == "IsTruncated":
            truncated = (node.text or "").strip().lower() == "true"
        elif name == "NextContinuationToken":
            token = node.text
    return objects, (token if truncated and token else None)


def list_objects(
    config: R2Config,
    prefix: str | None = None,
) -> list[RemoteObject]:
    """列出前缀下的全部对象（自动翻页）。"""
    if not config.is_complete:
        raise ValueError("R2 配置不完整")
    results: list[RemoteObject] = []
    token: str | None = None
    while True:
        query = {"list-type": "2", "max-keys": str(LIST_PAGE_SIZE)}
        if prefix:
            query["prefix"] = prefix
        if token:
            query["continuation-token"] = token
        data = _request(config, "GET", query=query)
        objects, next_token = parse_list_response(data)
        results.extend(objects)
        if not next_token:
            break
        token = next_token
    return results


def download(config: R2Config, object_key: str, dest_path: str | Path) -> None:
    """把对象下载到本地文件；先写临时文件，成功后再替换。"""
    if not config.is_complete:
        raise ValueError("R2 配置不完整")
    target = Path(dest_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    host, canonical_uri = _build_target(config, object_key)
    amz_date, authorization, canonical_query = _sign(
        config, "GET", canonical_uri, {}, EMPTY_SHA256, None
    )
    path = canonical_uri
    if canonical_query:
        path += "?" + canonical_query
    headers = {
        "Host": host,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": EMPTY_SHA256,
        "Authorization": authorization,
    }

    connection = http.client.HTTPSConnection(host, timeout=TIMEOUT_SECONDS)
    fd, temp_path = tempfile.mkstemp(
        prefix=".download.", dir=str(target.parent)
    )
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        if response.status >= 300:
            data = response.read()
            raise R2Error(
                response.status, _extract_error(data, response.reason)
            )
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
        os.replace(temp_path, target)
    except R2Error:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise R2NetworkError(str(exc) or exc.__class__.__name__) from None
    finally:
        connection.close()
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def delete_object(config: R2Config, object_key: str) -> None:
    """删除对象；对象本来就不存在（404）时视为成功。"""
    if not config.is_complete:
        raise ValueError("R2 配置不完整")
    try:
        _request(config, "DELETE", object_key=object_key)
    except R2Error as exc:
        if exc.code == 404:
            return
        raise


def object_exists(config: R2Config, object_key: str) -> bool:
    """确认对象是否仍存在；404 返回 False。"""
    if not config.is_complete:
        raise ValueError("R2 配置不完整")
    try:
        _request(config, "HEAD", object_key=object_key)
    except R2Error as exc:
        if exc.code == 404:
            return False
        raise
    return True
