"""Extract files from AICAM BUNDLE v1 (aicam.txt) into a directory tree."""

import base64
import hashlib
import re
from pathlib import Path

HEADER_RE = re.compile(
    rb"^=== FILE path=(.+?) encoding=(utf8|base64) size=(\d+) "
    rb"orig_bytes=(\d+) sha256=([a-f0-9]+) ===\n",
    re.MULTILINE,
)

BASE_DIR = Path(__file__).resolve().parent
BUNDLE_PATH = BASE_DIR / "aicam.txt"
OUT_ROOT = BASE_DIR / "unbundled"


def unbundle(bundle_path: Path, out_root: Path) -> int:
    raw = bundle_path.read_bytes()
    matches = list(HEADER_RE.finditer(raw))
    if not matches:
        raise SystemExit("No file entries found in bundle")

    extracted = 0
    for i, m in enumerate(matches):
        rel_path = m.group(1).decode("utf-8")
        encoding = m.group(2).decode("ascii")
        size = int(m.group(3))
        orig_bytes = int(m.group(4))
        expected_sha = m.group(5).decode("ascii")

        start = m.end()
        end = start + size
        if end > len(raw):
            raise SystemExit(f"Truncated payload for {rel_path}")
        payload = raw[start:end]
        if end < len(raw) and raw[end : end + 1] != b"\n":
            raise SystemExit(
                f"Missing newline after payload for {rel_path} at offset {end}"
            )

        if len(payload) != size:
            raise SystemExit(f"Size mismatch for {rel_path}")

        if encoding == "utf8":
            content = payload
        elif encoding == "base64":
            content = base64.b64decode(payload, validate=True)
        else:
            raise SystemExit(f"Unknown encoding {encoding!r} for {rel_path}")

        if len(content) != orig_bytes:
            raise SystemExit(
                f"orig_bytes mismatch for {rel_path}: got {len(content)}, expected {orig_bytes}"
            )

        digest = hashlib.sha256(content).hexdigest()
        if digest != expected_sha:
            raise SystemExit(
                f"SHA256 mismatch for {rel_path}: got {digest}, expected {expected_sha}"
            )

        dest = (out_root / rel_path).resolve()
        if not str(dest).startswith(str(out_root.resolve())):
            raise SystemExit(f"Unsafe path: {rel_path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        extracted += 1

    return extracted


def main() -> None:
    if not BUNDLE_PATH.is_file():
        raise SystemExit(f"Bundle not found: {BUNDLE_PATH}")
    if OUT_ROOT.exists():
        # merge/overwrite per file
        pass
    else:
        OUT_ROOT.mkdir(parents=True)

    count = unbundle(BUNDLE_PATH, OUT_ROOT)
    print(f"Extracted {count} files to {OUT_ROOT}")


if __name__ == "__main__":
    main()
