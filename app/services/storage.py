"""Object storage (MinIO / S3 in dev and demo; an in-memory dict in tests)."""

from functools import lru_cache
from typing import Any

from app.core.config import get_settings

_MEMORY: dict[str, tuple[bytes, str]] = {}


class StorageError(Exception):
    pass


@lru_cache
def _client() -> Any:
    import boto3
    from botocore.config import Config

    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint_url,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", connect_timeout=3, retries={"max_attempts": 2}),
    )


def _memory() -> bool:
    return get_settings().storage_backend == "memory"


def put(key: str, data: bytes, content_type: str) -> None:
    if _memory():
        _MEMORY[key] = (data, content_type)
        return
    try:
        _client().put_object(
            Bucket=get_settings().s3_bucket, Key=key, Body=data, ContentType=content_type
        )
    except Exception as exc:  # noqa: BLE001
        raise StorageError(str(exc)) from exc


def get(key: str) -> bytes | None:
    if _memory():
        item = _MEMORY.get(key)
        return item[0] if item else None
    try:
        obj = _client().get_object(Bucket=get_settings().s3_bucket, Key=key)
        return bytes(obj["Body"].read())
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404"):
            return None
        raise StorageError(str(exc)) from exc


def delete(key: str) -> None:
    if _memory():
        _MEMORY.pop(key, None)
        return
    try:
        _client().delete_object(Bucket=get_settings().s3_bucket, Key=key)
    except Exception as exc:  # noqa: BLE001
        raise StorageError(str(exc)) from exc


def clear_memory() -> None:
    _MEMORY.clear()
