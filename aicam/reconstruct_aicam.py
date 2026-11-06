"""Rebuild aicam.txt from split JSON parts listed in aicam.json."""

import hashlib
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def split_paths() -> list[Path]:
    """Map manifest part numbers to on-disk split files (sorted by part)."""
    by_part: dict[int, Path] = {}
    for path in BASE_DIR.glob("*.json"):
        if path.name == "aicam.json":
            continue
        with open(path, encoding="utf-8") as f:
            part = json.load(f)["part"]
        by_part[part] = path
    return [by_part[i] for i in range(1, len(by_part) + 1)]


def main() -> None:
    manifest = json.loads((BASE_DIR / "aicam.json").read_text(encoding="utf-8"))
    expected_size = manifest["original_file_size_bytes"]
    expected_sha = manifest["sha256"]

    hex_parts: list[str] = []
    for path in split_paths():
        with open(path, encoding="utf-8") as f:
            hex_parts.append(json.load(f)["data"])

    data = bytes.fromhex("".join(hex_parts))
    if len(data) != expected_size:
        raise SystemExit(f"Size mismatch: got {len(data)}, expected {expected_size}")
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha:
        raise SystemExit(f"SHA256 mismatch: got {digest}")

    out = BASE_DIR / manifest["original_file_name"]
    out.write_bytes(data)
    print(f"Wrote {out} ({len(data)} bytes, sha256 OK)")


if __name__ == "__main__":
    main()
