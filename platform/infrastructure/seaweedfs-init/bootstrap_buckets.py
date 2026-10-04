"""Creates the platform's 3 object-storage buckets against whatever S3-
compatible backend S3_ENDPOINT_URL points at — idempotent (a bucket that
already exists is a no-op, not an error), so this is safe to re-run on
every `docker compose up` the same way `alembic upgrade head` is.

Bind-mounted (not baked into the api image) and run with that image's own
python + reo_common + boto3, via `docker-compose.yml`'s `seaweedfs-init`
service — the same "reuse the api image for a one-shot task" pattern
`migrate` already uses for Alembic.
"""

from __future__ import annotations

import sys
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from reo_common.config import get_settings

settings = get_settings()


def main() -> None:
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )
    buckets = [settings.s3_bucket_exports, settings.s3_bucket_lakehouse, settings.s3_bucket_quarantine]
    for bucket in buckets:
        _create_with_retry(client, bucket)


def _create_with_retry(client, bucket: str, attempts: int = 45, delay_seconds: float = 2.0) -> None:
    """SeaweedFS reports healthy (its master is up) a little before its S3
    gateway accepts connections, so the first call can legitimately fail —
    wait for the gateway instead of failing the whole stack start."""
    for attempt in range(1, attempts + 1):
        try:
            client.create_bucket(Bucket=bucket)
            print(f"created bucket {bucket!r}")
            return
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                print(f"bucket {bucket!r} already exists — skipping")
                return
            if attempt == attempts:
                raise
        except BotoCoreError as exc:  # connection refused / endpoint not up yet
            if attempt == attempts:
                raise
            print(f"object store not ready yet ({type(exc).__name__}) — retrying in {delay_seconds:.0f}s [{attempt}/{attempts}]", file=sys.stderr)
        time.sleep(delay_seconds)


if __name__ == "__main__":
    main()
