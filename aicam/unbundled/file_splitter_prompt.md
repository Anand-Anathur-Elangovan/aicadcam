# File Splitter Prompt

## Usage
When you need to split any large file into multiple parts under 128KB each with base64 encoding, use this prompt:

---

## Prompt to Copy-Paste:

```
I need to split a file located at: [FILE_PATH]

Requirements:
1. Read the entire file (with its name and relative location)
2. Encode it to base64
3. Split into multiple files, each under 128KB max
4. Create a main metadata file with all information
5. Each split file should contain:
   - Part number
   - Total parts count
   - Base64 encoded data
6. Main metadata file should contain:
   - Original file name
   - Original file size
   - File relative path
   - Total number of splits
   - List of all split files

Output location: [OUTPUT_DIRECTORY]
Main metadata file name: [MAIN_FILE_NAME] (e.g., "metadata.json")
```

## Example:
```
I need to split a file located at: /Users/ANATAN8/Desktop/project/sqleditorapi/main.py

Requirements:
1. Read the entire file (with its name and relative location)
2. Encode it to base64
3. Split into multiple files, each under 128KB max
4. Create a main metadata file with all information
5. Each split file should contain part number, total count, and base64 data
6. Main metadata file should have original name, size, path, total splits, and file list

Output location: /Users/ANATAN8/Desktop/project/sqleditorapi/splits
Main metadata file name: main_metadata.json
```

---

## What You'll Get:
- ✅ `main_metadata.json` - Master file with all metadata
- ✅ `split_1.json` - Part 1 (under 128KB)
- ✅ `split_2.json` - Part 2 (under 128KB)
- ✅ `split_N.json` - Part N (under 128KB)

All files are JSON with base64 encoding - safe to upload anywhere!
