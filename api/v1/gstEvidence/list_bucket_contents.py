"""List objects under the configured AWS S3 key prefix (read-only)."""

import os
import sys
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[3] / ".env")

S3_REGION = os.getenv("S3_REGION")
BUCKET_NAME = os.getenv("S3_DEFAULT_BUCKET")
S3_KEY_PREFIX = os.getenv("S3_KEY_PREFIX", "").strip("/")


def main() -> int:
    if not S3_REGION or not BUCKET_NAME:
        print("Missing S3_REGION or S3_DEFAULT_BUCKET in the repository .env or process environment.")
        return 2

    s3 = boto3.client(
        "s3",
        region_name=S3_REGION,
        config=Config(connect_timeout=5, read_timeout=30),
    )

    try:
        paginator = s3.get_paginator("list_objects_v2")
        count = 0
        total_bytes = 0

        print(f"Objects in bucket '{BUCKET_NAME}' under '{S3_KEY_PREFIX or '/'}':")
        for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=S3_KEY_PREFIX):
            for item in page.get("Contents", []):
                size = item["Size"]
                total_bytes += size
                count += 1
                print(
                    f"{item['Key']} | {size:,} bytes | "
                    f"last modified {item['LastModified']}"
                )

        if count == 0:
            print("The bucket is empty (or contains no objects visible to these credentials).")
        print(f"\nTotal: {count} object(s), {total_bytes:,} bytes")
        return 0
    except (ClientError, BotoCoreError) as exc:
        print(f"Could not list bucket '{BUCKET_NAME}': {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
