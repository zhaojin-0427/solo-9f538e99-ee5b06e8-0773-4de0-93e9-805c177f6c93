#!/usr/bin/env python3
"""Optional first-boot demo seeder.

Uploads the bundled sample taxonomy package and the four sample XBRL
instances (feasible / rounding / infeasible / pending) so that /docs has
data to explore immediately. Idempotent: skips when a marker exists.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

BASE = os.environ.get("XRECON_BASE_URL", "http://127.0.0.1:8000")
DATA_DIR = Path(os.environ.get("XRECON_DATA_DIR", "/data"))
_default_samples = Path(__file__).resolve().parents[1] / "samples"
if not _default_samples.exists():  # local checkout layout
    _default_samples = Path(__file__).resolve().parents[1] / "tests" / "samples"
SAMPLES = Path(os.environ.get("XRECON_SAMPLES_DIR", str(_default_samples)))
MARKER = DATA_DIR / ".seeded"


def _post(url: str, fields: dict[str, str], files: dict[str, tuple[str, bytes]]):
    boundary = f"----xrecon{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n".encode()
        )
    for name, (filename, content) in files.items():
        body.extend(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
            f"filename=\"{filename}\"\r\nContent-Type: application/xml\r\n\r\n".encode()
        )
        body.extend(content)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        url, data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read()


def _wait_for_api(retries: int = 30) -> None:
    for _ in range(retries):
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=3) as resp:
                if resp.status == 200:
                    return
        except OSError:
            time.sleep(1)
    raise RuntimeError("API did not become healthy in time")


def main() -> int:
    if MARKER.exists():
        print("[seed] marker found, skipping")
        return 0
    _wait_for_api()
    xsd = (SAMPLES / "acct.xsd").read_bytes()
    calc = (SAMPLES / "acct-calc.xml").read_bytes()
    status, raw = _post(
        f"{BASE}/packages",
        {"name": "demo-accounting"},
        {"xsd": ("acct.xsd", xsd), "calculation": ("acct-calc.xml", calc)},
    )
    assert status == 201, raw
    package_id = json.loads(raw)["package_id"]
    for scenario in ("feasible", "rounding", "infeasible", "pending"):
        xml = (SAMPLES / f"instance-{scenario}.xml").read_bytes()
        status, raw = _post(
            f"{BASE}/packages/{package_id}/instances",
            {},
            {"instance": (f"instance-{scenario}.xml", xml)},
        )
        assert status == 201, raw
        print(f"[seed] uploaded instance-{scenario}")
    MARKER.touch()
    print(f"[seed] done; package_id={package_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
