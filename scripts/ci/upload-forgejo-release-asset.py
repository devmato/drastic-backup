#!/usr/bin/env python3

import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"ABBRUCH: {name} is required")
    return value


server_url = required_env("GITHUB_SERVER_URL").rstrip("/")
repository = required_env("GITHUB_REPOSITORY")
tag_name = required_env("GITHUB_REF_NAME")
token = required_env("FORGEJO_TOKEN")
asset_path = Path(required_env("ASSET_PATH"))

if not asset_path.is_file():
    raise SystemExit(f"ABBRUCH: Asset not found: {asset_path}")

api_base = f"{server_url}/api/v1"
release_base = f"{api_base}/repos/{repository}/releases"
headers = {
    "Accept": "application/json",
    "Authorization": f"token {token}",
}


def request_json(method: str, url: str, data: dict | None = None) -> dict:
    body = None
    request_headers = dict(headers)
    if data is not None:
        body = json.dumps(data).encode()
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode())


try:
    release = request_json("GET", f"{release_base}/tags/{urllib.parse.quote(tag_name)}")
except urllib.error.HTTPError as exc:
    if exc.code != 404:
        raise
    release = request_json(
        "POST",
        release_base,
        {
            "tag_name": tag_name,
            "target_commitish": os.environ.get("GITHUB_SHA", ""),
            "name": tag_name,
            "body": f"Release {tag_name}",
            "draft": False,
            "prerelease": "-" in tag_name,
        },
    )

release_id = release["id"]
asset_name = asset_path.name
boundary = "----drastic-forgejo-release-boundary"
content_type = mimetypes.guess_type(asset_name)[0] or "application/octet-stream"
file_content = asset_path.read_bytes()
body = b"".join(
    [
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="attachment"; filename="{asset_name}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        file_content,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
)

upload_url = f"{release_base}/{release_id}/assets?name={urllib.parse.quote(asset_name)}"
upload_headers = dict(headers)
upload_headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
request = urllib.request.Request(upload_url, data=body, headers=upload_headers, method="POST")

try:
    with urllib.request.urlopen(request) as response:
        response.read()
except urllib.error.HTTPError as exc:
    if exc.code == 409:
        print(f"Asset already exists on release {tag_name}: {asset_name}")
        sys.exit(0)
    raise

print(f"Uploaded {asset_name} to release {tag_name}")
