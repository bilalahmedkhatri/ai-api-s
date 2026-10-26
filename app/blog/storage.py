"""Dedicated public blog storage. Never falls back to audio/log B2 credentials."""

import os
import re
from dataclasses import dataclass, field
from urllib.parse import quote

import aioboto3
from botocore.config import Config
from dotenv import dotenv_values
from fastapi import HTTPException

from app.blog.config import ENV_FILE
from app.blog.content import safe_url


@dataclass(frozen=True)
class MediaStorage:
    bucket: str
    endpoint: str
    public_base: str
    key_id: str = field(repr=False)
    application_key: str = field(repr=False)

    def client(self):
        region = self.endpoint.split("s3.", 1)[1].split(".backblazeb2.com")[0]
        return aioboto3.Session().client(
            "s3",
            endpoint_url=self.endpoint,
            aws_access_key_id=self.key_id,
            aws_secret_access_key=self.application_key,
            region_name=region,
            config=Config(
                signature_version="s3v4",
                connect_timeout=10,
                read_timeout=30,
                retries={"total_max_attempts": 1},
            ),
        )

    def url(self, key):
        return self.public_base + "/" + quote(key, safe="/")


def storage_configuration(environment=None):
    values = {**dotenv_values(ENV_FILE), **os.environ} if environment is None else environment
    names = (
        "BUCKET_NAME",
        "ENDPOINT_URL",
        "PUBLIC_BASE_URL",
        "APPLICATION_KEY_ID",
        "APPLICATION_KEY",
    )
    bucket, endpoint, public, key_id, key = [
        str(values.get("BLOG_B2_" + name) or "").strip() for name in names
    ]
    if not all((bucket, endpoint, public, key_id, key)):
        raise HTTPException(503, "Configure dedicated BLOG_B2 settings on the admin backend.")
    try:
        safe_url(public)
        if (
            "?" in public
            or "#" in public
            or not re.fullmatch(r"https://s3\.[a-z0-9-]+\.backblazeb2\.com/?", endpoint)
        ):
            raise ValueError()
        if not re.fullmatch(r"[A-Za-z0-9-]{6,63}", bucket):
            raise ValueError()
    except ValueError:
        raise HTTPException(503, "Invalid blog B2 endpoint, bucket or public base URL.") from None
    return MediaStorage(bucket, endpoint.rstrip("/"), public.rstrip("/"), key_id, key)
