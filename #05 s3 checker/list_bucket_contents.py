"""List objects in the compliance S3 bucket (read-only)."""

import os
import sys
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv


load_dotenv(Path(__file__).with_name(".env"))

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://192.168.10.100:8333")
S3_REGION = os.getenv("S3_REGION", "us-east-1")
S3_ACCESS_KEY = os.getenv("S3_ADMIN_ACCESS")
S3_SECRET_KEY = os.getenv("S3_ADMIN_SECRET")
BUCKET_NAME = "compliance"


def main() -> int:
    if not S3_ACCESS_KEY or not S3_SECRET_KEY:
        print("Missing S3_ADMIN_ACCESS or S3_ADMIN_SECRET in .env or the process environment.")
        return 2

    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        region_name=S3_REGION,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=Config(connect_timeout=5, read_timeout=30),
    )

    try:
        paginator = s3.get_paginator("list_objects_v2")
        count = 0
        total_bytes = 0

        print(f"Objects in bucket '{BUCKET_NAME}':")
        for page in paginator.paginate(Bucket=BUCKET_NAME):
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
