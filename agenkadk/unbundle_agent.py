"""Extract files from agent.txt bundle into unbundled/."""

import hashlib
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BUNDLE_PATH = BASE_DIR / "agent.txt"
OUT_ROOT = BASE_DIR / "unbundled"

HEADER_RE = re.compile(
    rb"^===== FILE: (.+?) \| size=(\d+) \| sha256=([a-f0-9]+) =====\n",
    re.MULTILINE,
)


def unbundle(bundle_path: Path, out_root: Path) -> int:
    raw = bundle_path.read_bytes()
    matches = list(HEADER_RE.finditer(raw))
    if not matches:
        raise SystemExit("No file entries found")

    out_root.mkdir(parents=True, exist_ok=True)
    extracted = 0

    for m in matches:
        rel_path = m.group(1).decode("utf-8")
        size = int(m.group(2))
        expected_sha = m.group(3).decode("ascii")

        start = m.end()
        payload = raw[start : start + size]
        if len(payload) != size:
            raise SystemExit(f"Truncated payload for {rel_path}")

        digest = hashlib.sha256(payload).hexdigest()
        if digest != expected_sha:
            raise SystemExit(f"SHA256 mismatch for {rel_path}")

        dest = (out_root / rel_path).resolve()
        if not str(dest).startswith(str(out_root.resolve())):
            raise SystemExit(f"Unsafe path: {rel_path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        extracted += 1

    return extracted


def main() -> None:
    if not BUNDLE_PATH.is_file():
        raise SystemExit(f"Run reconstruct_agent.py first — missing {BUNDLE_PATH}")
    count = unbundle(BUNDLE_PATH, OUT_ROOT)
    print(f"Extracted {count} files to {OUT_ROOT}")


if __name__ == "__main__":
    main()
