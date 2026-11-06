"""Extract files from emailvad.txt (BEGIN/END markers) into unbundled/."""

import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BUNDLE_PATH = BASE_DIR / "emailvad.txt"
OUT_ROOT = BASE_DIR / "unbundled"

BEGIN_RE = re.compile(rb"^=====BEGIN FILE: (.+?)=====\n", re.MULTILINE)
END_RE = re.compile(rb"^=====END FILE: (.+?)=====\n", re.MULTILINE)


def unbundle(bundle_path: Path, out_root: Path) -> int:
    raw = bundle_path.read_bytes()
    begins = list(BEGIN_RE.finditer(raw))
    ends = list(END_RE.finditer(raw))
    if len(begins) != len(ends):
        raise SystemExit(f"Mismatched markers: {len(begins)} BEGIN, {len(ends)} END")

    out_root.mkdir(parents=True, exist_ok=True)
    extracted = 0

    for b, e in zip(begins, ends):
        rel_path = b.group(1).decode("utf-8")
        end_path = e.group(1).decode("utf-8")
        if rel_path != end_path:
            raise SystemExit(f"Path mismatch: {rel_path!r} vs {end_path!r}")

        content = raw[b.end() : e.start()]
        dest = (out_root / rel_path).resolve()
        if not str(dest).startswith(str(out_root.resolve())):
            raise SystemExit(f"Unsafe path: {rel_path}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        extracted += 1

    return extracted


def main() -> None:
    if not BUNDLE_PATH.is_file():
        raise SystemExit(f"Run reconstruct_emailvad.py first — missing {BUNDLE_PATH}")
    count = unbundle(BUNDLE_PATH, OUT_ROOT)
    print(f"Extracted {count} files to {OUT_ROOT}")


if __name__ == "__main__":
    main()
