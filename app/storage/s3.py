"""
app/storage/s3.py
S3-compatible object storage provider (AWS S3, Cloudflare R2, MinIO,
Supabase Storage's S3 interface, etc.) via boto3 — no vendor SDKs.
"""

from typing import Dict, List

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

import config
from app.storage.service import StorageProvider


class S3StorageProvider(StorageProvider):
    def __init__(self):
        self.bucket = config.STORAGE_BUCKET
        self.client = boto3.client(
            "s3",
            endpoint_url=config.STORAGE_ENDPOINT_URL or None,
            region_name=config.STORAGE_REGION or None,
            aws_access_key_id=config.STORAGE_ACCESS_KEY,
            aws_secret_access_key=config.STORAGE_SECRET_KEY,
            config=BotoConfig(signature_version="s3v4"),
        )

    def upload(self, key: str, content: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content, ContentType=content_type)

    def download(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, keys: List[str]) -> None:
        # single-object delete for compatibility with S3 gateways that reject DeleteObjects
        for key in keys:
            self.client.delete_object(Bucket=self.bucket, Key=key)

    def copy(self, src_key: str, dst_key: str, content_type: str = None) -> None:
        self.client.copy_object(Bucket=self.bucket, CopySource={"Bucket": self.bucket, "Key": src_key}, Key=dst_key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def signed_url(self, key: str, expires_in: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_in
        )

    def signed_urls_bulk(self, keys: List[str], expires_in: int = 3600) -> Dict[str, str]:
        # Presigning is a local HMAC computation, not a network call — a loop
        # here is already O(1) round trips, no bulk API needed.
        return {key: self.signed_url(key, expires_in) for key in keys}
