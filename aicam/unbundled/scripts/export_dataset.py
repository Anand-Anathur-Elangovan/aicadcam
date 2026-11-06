#!/usr/bin/env python3
"""
export_dataset.py — Export human-approved G-code diffs to JSONL training dataset.

Reads all approved gcode_diffs from PostgreSQL where embedded_in_vector=true
and used_in_training=false, formats them in Alpaca/ShareGPT JSONL format
for LoRA fine-tuning, and writes to the output directory.

Usage:
    python scripts/export_dataset.py --output ./data/training --min-edits 1

Requirements:
    pip install psycopg2-binary python-dotenv
"""

import os
import json
import argparse
import hashlib
from datetime import datetime
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2-binary not installed. Run: pip install psycopg2-binary")
    exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass   # dotenv optional

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/manufacturing_ai")


def build_instruction(row: dict) -> str:
    """Build the instruction prompt from part metadata."""
    parts = ["Generate CNC G-code for the following part:"]
    if row.get("part_name"):      parts.append(f"Part Name: {row['part_name']}")
    if row.get("part_number"):    parts.append(f"Part Number: {row['part_number']}")
    if row.get("revision"):       parts.append(f"Revision: {row['revision']}")
    if row.get("material"):       parts.append(f"Material: {row['material']}")
    if row.get("machine_type"):   parts.append(f"Machine: {row['machine_type']}")

    if row.get("generated_plan_json"):
        plan = row["generated_plan_json"]
        if isinstance(plan, dict) and "operations" in plan:
            ops = plan["operations"]
            parts.append(f"\nProcess Plan ({len(ops)} operations):")
            for op in ops[:6]:   # cap at 6 to avoid token explosion
                parts.append(
                    f"  OP{op.get('opNumber', '?')}: {op.get('name', '')} — "
                    f"Tool: {op.get('tool', {}).get('type', '?')} "
                    f"Ø{op.get('tool', {}).get('diameter', '?')}mm, "
                    f"S{op.get('cuttingParams', {}).get('spindleSpeed', '?')} "
                    f"F{op.get('cuttingParams', {}).get('feedRate', '?')}"
                )

    if row.get("edit_reason"):
        parts.append(f"\nEngineer Preference: {row['edit_reason']}")

    return "\n".join(parts)


def build_alpaca_sample(row: dict) -> dict:
    """Format as Alpaca instruction-response pair."""
    return {
        "instruction": build_instruction(row),
        "input":       "",
        "output":      row["approved_gcode"],
        "metadata": {
            "diff_id":        str(row["id"]),
            "part_number":    row.get("part_number"),
            "material":       row.get("material"),
            "lines_added":    row.get("lines_added", 0),
            "lines_removed":  row.get("lines_removed", 0),
            "quality_rating": row.get("quality_rating"),
            "geometry_hash":  row.get("part_geometry_hash"),
            "approved_at":    str(row.get("approved_at")),
        }
    }


def build_sharegpt_sample(row: dict) -> dict:
    """Format as ShareGPT conversation pair."""
    system_msg = (
        "You are an expert CNC programmer. Generate machine-ready Fanuc/Haas G-code "
        "based on the part description and process plan. Output ONLY the G-code, "
        "starting with % and ending with %."
    )
    return {
        "system": system_msg,
        "conversations": [
            {"from": "human",  "value": build_instruction(row)},
            {"from": "gpt",    "value": row["approved_gcode"]},
        ],
        "metadata": {
            "diff_id":     str(row["id"]),
            "part_number": row.get("part_number"),
            "material":    row.get("material"),
        }
    }


def export_dataset(
    output_dir: str,
    min_edits: int = 1,
    format: str = "alpaca",
    mark_used: bool = True,
) -> dict:
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT id, approved_at, part_number, part_name, revision, material, machine_type,
               part_geometry_hash, generated_gcode, generated_plan_json, approved_gcode,
               edit_reason, lines_added, lines_removed, quality_rating
        FROM gcode_diffs
        WHERE approved_gcode IS NOT NULL
          AND used_in_training = false
          AND (lines_added + lines_removed) >= %s
        ORDER BY approved_at
    """, (min_edits,))

    rows = cur.fetchall()

    if not rows:
        print("No new approved diffs to export.")
        conn.close()
        return {"exported": 0}

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = out_path / f"gcode_diffs_{timestamp}_{format}.jsonl"
    ids       = []

    with open(filename, "w", encoding="utf-8") as f:
        for row in rows:
            sample = build_alpaca_sample(row) if format == "alpaca" else build_sharegpt_sample(row)
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")
            ids.append(str(row["id"]))

    # Mark as used
    if mark_used and ids:
        cur.execute(
            "UPDATE gcode_diffs SET used_in_training = true WHERE id = ANY(%s::uuid[])",
            (ids,)
        )
        conn.commit()

    cur.close()
    conn.close()

    checksum = hashlib.sha256(open(filename, "rb").read()).hexdigest()
    print(f"Exported {len(rows)} samples → {filename}")
    print(f"SHA-256: {checksum}")

    return {
        "exported":  len(rows),
        "file":      str(filename),
        "format":    format,
        "checksum":  checksum,
        "diff_ids":  ids,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export G-code diff dataset for LoRA training")
    parser.add_argument("--output",    default="./data/training",  help="Output directory")
    parser.add_argument("--min-edits", default=1,  type=int,       help="Minimum lines changed to include")
    parser.add_argument("--format",    default="alpaca", choices=["alpaca", "sharegpt"])
    parser.add_argument("--dry-run",   action="store_true",        help="Export but don't mark as used")
    args = parser.parse_args()

    result = export_dataset(
        output_dir=args.output,
        min_edits=args.min_edits,
        format=args.format,
        mark_used=not args.dry_run,
    )
    print(json.dumps(result, indent=2))
