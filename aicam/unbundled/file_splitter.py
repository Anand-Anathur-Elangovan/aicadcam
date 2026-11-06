#!/usr/bin/env python3
"""
Universal File Splitter with Base64 Encoding
Splits any file into multiple JSON files under 128KB each
"""

import json
import base64
import os
import sys
from pathlib import Path
from datetime import datetime


def split_file(file_path, output_dir, metadata_name="metadata.json", max_size_kb=128):
    """
    Split a file into multiple JSON files with base64 encoding

    Args:
        file_path: Path to the file to split
        output_dir: Directory to save split files
        metadata_name: Name of the main metadata file
        max_size_kb: Maximum size per split file in KB
    """

    file_path = Path(file_path).resolve()
    output_dir = Path(output_dir).resolve()

    # Validate input file
    if not file_path.exists():
        print(f"❌ Error: File not found: {file_path}")
        return False

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Read and encode file
    print(f"📖 Reading file: {file_path}")
    with open(file_path, 'rb') as f:
        original_data = f.read()

    print(f"🔐 Encoding to base64...")
    encoded_data = base64.b64encode(original_data).decode('utf-8')

    # Calculate split size (in characters, accounting for JSON overhead)
    max_bytes_per_file = max_size_kb * 1024
    # Rough estimate: each character + JSON overhead
    max_data_size = int(max_bytes_per_file * 0.95)  # Leave 5% for JSON structure

    # Split data
    parts = []
    for i in range(0, len(encoded_data), max_data_size):
        parts.append(encoded_data[i:i + max_data_size])

    print(f"✂️  Split into {len(parts)} parts")

    # Get relative path
    try:
        relative_path = str(file_path.relative_to(Path.home()))
    except ValueError:
        relative_path = str(file_path)

    # Create split files
    split_files = []
    for idx, part in enumerate(parts, 1):
        split_filename = f"split_{idx:04d}.json"
        split_filepath = output_dir / split_filename

        split_data = {
            "part": idx,
            "total_parts": len(parts),
            "original_file": file_path.name,
            "original_size_bytes": len(original_data),
            "data": part
        }

        with open(split_filepath, 'w') as f:
            json.dump(split_data, f)

        file_size = len(json.dumps(split_data))
        split_files.append({
            "part": idx,
            "filename": split_filename,
            "size_bytes": file_size
        })

        print(f"   ✅ {split_filename} - {file_size:,} bytes")

    # Create metadata file
    metadata = {
        "created": datetime.now().isoformat(),
        "original_file_name": file_path.name,
        "original_file_size_bytes": len(original_data),
        "original_file_path": str(file_path),
        "relative_path": relative_path,
        "encoded_size_bytes": len(encoded_data),
        "encoding": "base64",
        "total_splits": len(parts),
        "max_split_size_kb": max_size_kb,
        "split_files": split_files,
        "output_directory": str(output_dir),
        "reconstruction_script": """
import json
import base64
from pathlib import Path

# Load all split files
parts = []
output_dir = Path('.')
for i in range(1, total_splits + 1):
    split_file = output_dir / f'split_{i:04d}.json'
    with open(split_file, 'r') as f:
        data = json.load(f)
        parts.append(data['data'])

# Combine and decode
full_base64 = ''.join(parts)
original_data = base64.b64decode(full_base64)

# Save recovered file
with open('original_filename.ext', 'wb') as f:
    f.write(original_data)

print("✅ File reconstructed!")
"""
    }

    metadata_filepath = output_dir / metadata_name
    with open(metadata_filepath, 'w') as f:
        json.dump(metadata, f, indent=2)

    print(f"\n📋 Metadata file created: {metadata_name}")
    print(f"\n✅ SUCCESS!")
    print(f"   Original file: {file_path.name} ({len(original_data):,} bytes)")
    print(f"   Split into: {len(parts)} files")
    print(f"   Output directory: {output_dir}")
    print(f"   Total disk used: {sum(f['size_bytes'] for f in split_files):,} bytes")

    return True


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 file_splitter.py <input_file> <output_directory> [metadata_name] [max_size_kb]")
        print("\nExample:")
        print("  python3 file_splitter.py /path/to/large_file.py ./splits metadata.json 128")
        sys.exit(1)

    file_path = sys.argv[1]
    output_dir = sys.argv[2]
    metadata_name = sys.argv[3] if len(sys.argv) > 3 else "metadata.json"
    max_size_kb = int(sys.argv[4]) if len(sys.argv) > 4 else 128

    split_file(file_path, output_dir, metadata_name, max_size_kb)


if __name__ == "__main__":
    main()
