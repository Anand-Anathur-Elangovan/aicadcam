#!/bin/bash
# File Splitter Wrapper Script
# Usage: ./split.sh /path/to/file output_dir [metadata_name] [max_size_kb]

if [ $# -lt 2 ]; then
    echo "Usage: $0 <input_file> <output_directory> [metadata_name] [max_size_kb]"
    echo ""
    echo "Examples:"
    echo "  $0 /path/to/large_file.py ./splits"
    echo "  $0 main.py ./splits main_metadata.json 128"
    echo "  $0 data.csv ./splits_data data_meta.json 100"
    exit 1
fi

INPUT_FILE="$1"
OUTPUT_DIR="$2"
METADATA_NAME="${3:-metadata.json}"
MAX_SIZE="${4:-128}"

# Check if Python script exists
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPLITTER_SCRIPT="$SCRIPT_DIR/file_splitter.py"

if [ ! -f "$SPLITTER_SCRIPT" ]; then
    echo "❌ Error: file_splitter.py not found in $SCRIPT_DIR"
    exit 1
fi

# Run the Python splitter
python3 "$SPLITTER_SCRIPT" "$INPUT_FILE" "$OUTPUT_DIR" "$METADATA_NAME" "$MAX_SIZE"
