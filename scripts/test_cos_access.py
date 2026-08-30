#!/usr/bin/env python3
"""Test Chestnut's least-privilege COS credentials without exposing secrets."""

import os
import sys
from getpass import getpass
from datetime import datetime, timezone

from qcloud_cos import CosConfig, CosS3Client
from qcloud_cos.cos_exception import CosClientError, CosServiceError


def masked(value):
    value = str(value or "")
    if len(value) < 9:
        return "(configured)" if value else "(missing)"
    return f"{value[:5]}…{value[-4:]}"


def main():
    if not sys.stdin.isatty() and not all(os.environ.get(name) for name in (
        "CHESTNUT_COS_BUCKET", "CHESTNUT_COS_REGION",
        "CHESTNUT_COS_SECRET_ID", "CHESTNUT_COS_SECRET_KEY",
    )):
        print("COS variables are missing and interactive input is unavailable.", file=sys.stderr)
        return 2

    bucket = os.environ.get("CHESTNUT_COS_BUCKET", "").strip() or input("Bucket: ").strip()
    region = os.environ.get("CHESTNUT_COS_REGION", "").strip() or input("Region: ").strip()
    secret_id = os.environ.get("CHESTNUT_COS_SECRET_ID", "").strip() or input("SecretId: ").strip()
    secret_key = os.environ.get("CHESTNUT_COS_SECRET_KEY", "").strip() or getpass("SecretKey (hidden): ").strip()
    object_key = f"meetings/_diagnostics/cos-access-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.txt"

    print("Chestnut COS access diagnostic")
    print(f"  Bucket:   {bucket}")
    print(f"  Region:   {region}")
    print(f"  SecretId: {masked(secret_id)}")
    print(f"  Object:   {object_key}")
    print("  SecretKey: configured (never printed)")

    client = CosS3Client(CosConfig(
        Region=region,
        SecretId=secret_id,
        SecretKey=secret_key,
        Scheme="https",
    ))
    try:
        response = client.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=b"Chestnut COS access diagnostic. This file may be deleted safely.\n",
            ContentType="text/plain; charset=utf-8",
        )
    except CosServiceError as error:
        details = error.get_error_info()
        print("\nRESULT: COS rejected the request", file=sys.stderr)
        print(f"  Code:      {details.get('code', 'unknown')}", file=sys.stderr)
        print(f"  Message:   {details.get('message', 'unknown')}", file=sys.stderr)
        print(f"  Resource:  {details.get('resource', 'unknown')}", file=sys.stderr)
        print(f"  RequestId: {details.get('requestid', 'unknown')}", file=sys.stderr)
        return 1
    except CosClientError as error:
        print(f"\nRESULT: COS client/network error: {error}", file=sys.stderr)
        return 1

    print("\nRESULT: SUCCESS")
    print(f"  ETag: {response.get('ETag', '(not returned)')}")
    print("  The key, policy, region, and bucket are valid for PutObject.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
