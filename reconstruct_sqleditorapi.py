"""Reassemble sqleditorapi parts (JSON with base64 'data' fields) into one file."""

import base64
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = BASE_DIR / "sqleditorapi_recovered.txt"


def main() -> None:
    parts: list[str] = []

    for i in range(1, 4):
        path = BASE_DIR / f"sqleditorapi{i}.txt"
        with open(path, encoding="utf-8") as f:
            parts.append(json.load(f)["data"])

    for suffix in ("41", "42"):
        path = BASE_DIR / f"sqleditorapi{suffix}.txt"
        with open(path, encoding="utf-8") as f:
            parts.append(json.load(f)["data"])

    full_base64 = "".join(parts)
    original_data = base64.b64decode(full_base64)

    with open(OUTPUT_PATH, "wb") as f:
        f.write(original_data)

    print(f"Wrote {OUTPUT_PATH} ({len(original_data)} bytes)")


if __name__ == "__main__":
    main()
