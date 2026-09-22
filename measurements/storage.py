"""Private object storage for uploaded measurement CSV files."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import os
from pathlib import Path
import tempfile

from .settings import (
    AWS_REGION,
    DATA_DIR,
    OBJECT_CACHE_DIR,
    OBJECT_STORAGE_BACKEND,
    OBJECT_STORAGE_BUCKET,
    PRESIGNED_URL_SECONDS,
    S3_ENDPOINT_URL,
)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_object_path(object_key: str) -> Path:
    root = OBJECT_CACHE_DIR.resolve()
    target = (root / object_key).resolve()
    if root != target and root not in target.parents:
        raise ValueError("Unsafe object key")
    return target


class ObjectStorage:
    def __init__(self):
        self.backend = OBJECT_STORAGE_BACKEND
        self._client = None

    def _s3(self):
        if self._client is None:
            import boto3

            options = {"region_name": AWS_REGION}
            if S3_ENDPOINT_URL:
                options["endpoint_url"] = S3_ENDPOINT_URL
            self._client = boto3.client("s3", **options)
        return self._client

    def prepare_upload(self, object_key: str, sha256: str) -> dict:
        if self.backend == "filesystem":
            return {"method": "PUT", "headers": {}, "filesystem": True}
        headers = {"x-amz-meta-sha256": sha256}
        url = self._s3().generate_presigned_url(
            "put_object",
            Params={
                "Bucket": OBJECT_STORAGE_BUCKET,
                "Key": object_key,
                "Metadata": {"sha256": sha256},
            },
            ExpiresIn=PRESIGNED_URL_SECONDS,
            HttpMethod="PUT",
        )
        return {"method": "PUT", "url": url, "headers": headers, "filesystem": False}

    def receive_filesystem(self, object_key: str, source, size: int, sha256: str) -> None:
        if self.backend != "filesystem":
            raise RuntimeError("Direct content endpoint is disabled for S3 storage")
        target = _safe_object_path(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(target.suffix + ".partial")
        digest = hashlib.sha256()
        written = 0
        with partial.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > size:
                    raise ValueError("Upload exceeded declared size")
                digest.update(chunk)
                output.write(chunk)
        if written != size or not hmac.compare_digest(digest.hexdigest(), sha256):
            partial.unlink(missing_ok=True)
            raise ValueError("Uploaded size or SHA-256 does not match the manifest")
        partial.replace(target)

    def verify(self, object_key: str, size: int, sha256: str) -> None:
        if self.backend == "filesystem":
            target = _safe_object_path(object_key)
            if not target.is_file() or target.stat().st_size != size:
                raise ValueError("Stored object size does not match")
            if not hmac.compare_digest(sha256_path(target), sha256):
                raise ValueError("Stored object checksum does not match")
            return
        response = self._s3().head_object(Bucket=OBJECT_STORAGE_BUCKET, Key=object_key)
        if int(response["ContentLength"]) != size:
            raise ValueError("Stored object size does not match")
        stored_hash = str(response.get("Metadata", {}).get("sha256", "")).lower()
        if not hmac.compare_digest(stored_hash, sha256):
            raise ValueError("Stored object checksum metadata does not match")

    @contextmanager
    def materialize(self, object_key: str, size: int, sha256: str):
        if self.backend == "filesystem":
            target = _safe_object_path(object_key)
            self.verify(object_key, size, sha256)
            yield target
            return
        temp_root = DATA_DIR / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix="measurement-import-", suffix=".csv", dir=temp_root)
        os.close(handle)
        try:
            self._s3().download_file(OBJECT_STORAGE_BUCKET, object_key, name)
            target = Path(name)
            if target.stat().st_size != size or not hmac.compare_digest(
                sha256_path(target), sha256
            ):
                raise ValueError("Downloaded object failed size or checksum verification")
            yield target
        finally:
            Path(name).unlink(missing_ok=True)
