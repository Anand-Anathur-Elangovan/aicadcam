"""Rebuild emailvad.txt from hex split JSON parts (emailvad_metadata.json)."""

import hashlib
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
METADATA_PATH = BASE_DIR / "emailvad_metadata.json"


def load_parts() -> list[str]:
    by_part: dict[int, str] = {}
    for path in BASE_DIR.glob("*.json"):
        if path.name == METADATA_PATH.name:
            continue
        with open(path, encoding="utf-8") as f:
            chunk = json.load(f)
        by_part[int(chunk["part"])] = chunk["data"]
    total = len(by_part)
    return [by_part[i] for i in range(1, total + 1)]


def main() -> None:
    meta = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    data = bytes.fromhex("".join(load_parts()))

    if len(data) != meta["original_file_size_bytes"]:
        raise SystemExit(
            f"Size mismatch: got {len(data)}, expected {meta['original_file_size_bytes']}"
        )
    expected_sha = meta.get("original_sha256") or meta.get("sha256")
    if expected_sha:
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_sha:
            raise SystemExit(f"SHA256 mismatch: got {digest}")

    out = BASE_DIR / meta["original_file_name"]
    out.write_bytes(data)
    print(f"Wrote {out} ({len(data)} bytes, sha256 OK)")


if __name__ == "__main__":
    main()
