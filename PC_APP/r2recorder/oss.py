"""阿里云 OSS 的最小 REST 客户端（Signature V4，虚拟主机风格）。"""

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

from .config import OSSConfig

TIMEOUT_SECONDS = 300
LIST_PAGE_SIZE = 1000

V4_ALGORITHM = "OSS4-HMAC-SHA256"
V4_PRODUCT = "oss"
V4_REQUEST = "aliyun_v4_request"
UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"
_DEFAULT_SIGNED_HEADERS = ("content-type", "content-md5")

_CONTENT_TYPES = {
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
}


class OSSError(Exception):
    def __init__(self, code: int, detail: str):
        super().__init__(f"HTTP {code}: {detail}")
        self.code = code
        self.detail = detail

    @property
    def retryable(self) -> bool:
        return self.code in (408, 429) or self.code >= 500


class OSSNetworkError(Exception):
    """连接失败、超时等网络层错误。"""


def content_type_for(file_name: str) -> str:
    return _CONTENT_TYPES.get(Path(file_name).suffix.lower(), "application/octet-stream")


def _uri_encode(value: str, ignore_slashes: bool) -> str:
    safe = "-_.~"
    if ignore_slashes:
        safe += "/"
    return quote(value, safe=safe)


def _canonical_query(params: dict[str, str]) -> str:
    pairs = [
        (_uri_encode(str(key), False), _uri_encode(str(value), False))
        for key, value in params.items()
    ]
    pairs.sort(key=lambda pair: pair[0])
    return "&".join(f"{key}={value}" if value else key for key, value in pairs)


def _canonical_uri(config: OSSConfig, object_key: str | None) -> str:
    if object_key:
        resource = f"/{config.bucket}/{object_key}"
    else:
        resource = f"/{config.bucket}/"
    return _uri_encode(resource, True)


def _request_path(object_key: str | None) -> str:
    path = "/" + object_key if object_key else "/"
    return _uri_encode(path, True)


def _canonical_headers(headers: dict[str, str], additional=()) -> str:
    additional_names = {name.lower() for name in additional}
    pairs = []
    for name, value in headers.items():
        lower = name.lower()
        if (
            lower.startswith("x-oss-")
            or lower in _DEFAULT_SIGNED_HEADERS
            or lower in additional_names
        ):
            pairs.append((lower, str(value).strip()))
    pairs.sort(key=lambda pair: pair[0])
    return "".join(f"{name}:{value}\n" for name, value in pairs)


def _authorization(
    config: OSSConfig,
    method: str,
    object_key: str | None,
    query: dict[str, str],
    headers: dict[str, str],
    amz_date: str,
    additional=(),
) -> str:
    region = config.resolved_region
    if not region:
        raise ValueError("无法确定 OSS 地域（Region），请在设置中填写")

    date = amz_date[:8]
    canonical_headers = _canonical_headers(headers, additional)
    additional_value = ";".join(sorted({name.lower() for name in additional}))
    canonical_request = "\n".join(
        [
            method,
            _canonical_uri(config, object_key),
            _canonical_query(query),
            canonical_headers,
            additional_value,
            UNSIGNED_PAYLOAD,
        ]
    )

    scope = f"{date}/{region}/{V4_PRODUCT}/{V4_REQUEST}"
    string_to_sign = "\n".join(
        [
            V4_ALGORITHM,
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )

    signing_key = ("aliyun_v4" + config.access_key_secret).encode("utf-8")
    for part in (date, region, V4_PRODUCT, V4_REQUEST):
        signing_key = hmac.new(
            signing_key, part.encode("utf-8"), hashlib.sha256
        ).digest()
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    credential = f"{config.access_key_id}/{scope}"
    authorization = (
        f"{V4_ALGORITHM} Credential={credential}, Signature={signature}"
    )
    if additional_value:
        authorization += f", AdditionalHeaders={additional_value}"
    return authorization


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
    config: OSSConfig,
    method: str,
    *,
    object_key: str | None = None,
    query: dict[str, str] | None = None,
    body=None,
    content_type: str | None = None,
    content_length: int | None = None,
) -> bytes:
    if not config.is_complete:
        raise ValueError("OSS 配置不完整")

    host = config.request_host
    query = query or {}
    canonical_query = _canonical_query(query)
    path = _request_path(object_key)
    if canonical_query:
        path += "?" + canonical_query

    amz_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    signed_headers = {
        "x-oss-content-sha256": UNSIGNED_PAYLOAD,
        "x-oss-date": amz_date,
    }
    if content_type:
        signed_headers["Content-Type"] = content_type
    authorization = _authorization(
        config,
        method,
        object_key,
        query,
        signed_headers,
        amz_date,
    )

    headers = {
        "Host": host,
        **signed_headers,
        "Authorization": authorization,
    }
    if content_length is not None:
        headers["Content-Length"] = str(content_length)

    connection = http.client.HTTPSConnection(host, timeout=TIMEOUT_SECONDS)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        if response.status >= 300:
            raise OSSError(response.status, _extract_error(data, response.reason))
        return data
    except OSSError:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise OSSNetworkError(str(exc) or exc.__class__.__name__) from None
    finally:
        connection.close()


def upload(config: OSSConfig, file_path: str | Path, object_key: str) -> None:
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"本地文件不存在：{path}")
    size = path.stat().st_size
    with path.open("rb") as handle:
        _request(
            config,
            "PUT",
            object_key=object_key,
            body=handle,
            content_type=content_type_for(path.name),
            content_length=size,
        )


def test_connection(config: OSSConfig) -> None:
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
        raise OSSError(200, f"无法解析对象列表：{exc}") from None

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
    config: OSSConfig,
    prefix: str | None = None,
) -> list[RemoteObject]:
    """列出前缀下的全部对象（自动翻页）。"""
    if not config.is_complete:
        raise ValueError("OSS 配置不完整")
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


def download(config: OSSConfig, object_key: str, dest_path: str | Path) -> None:
    """把对象下载到本地文件；先写临时文件，成功后再替换。"""
    if not config.is_complete:
        raise ValueError("OSS 配置不完整")
    target = Path(dest_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    host = config.request_host
    path = _request_path(object_key)
    amz_date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    signed_headers = {
        "x-oss-content-sha256": UNSIGNED_PAYLOAD,
        "x-oss-date": amz_date,
    }
    authorization = _authorization(
        config,
        "GET",
        object_key,
        {},
        signed_headers,
        amz_date,
    )
    headers = {
        "Host": host,
        **signed_headers,
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
            raise OSSError(
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
    except OSSError:
        raise
    except (OSError, http.client.HTTPException) as exc:
        raise OSSNetworkError(str(exc) or exc.__class__.__name__) from None
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


def delete_object(config: OSSConfig, object_key: str) -> None:
    """删除对象；对象本来就不存在（404）时视为成功。"""
    if not config.is_complete:
        raise ValueError("OSS 配置不完整")
    try:
        _request(config, "DELETE", object_key=object_key)
    except OSSError as exc:
        if exc.code == 404:
            return
        raise


def object_exists(config: OSSConfig, object_key: str) -> bool:
    """确认对象是否仍存在；404 返回 False。"""
    if not config.is_complete:
        raise ValueError("OSS 配置不完整")
    try:
        _request(config, "HEAD", object_key=object_key)
    except OSSError as exc:
        if exc.code == 404:
            return False
        raise
    return True
