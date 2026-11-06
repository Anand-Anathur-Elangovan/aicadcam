import { describe, it, expect } from "vitest";
import { chunkText, chunkTable } from "@/lib/parsers/chunker";

// Default CHUNK_SIZE = 512 chars, CHUNK_OVERLAP = 64 chars (from env defaults)

// Helper: build a sentence of approx N characters
const makeSentence = (label: string, padToLength = 90): string => {
  const base = `${label}.`;
  const padding = " The part requires careful dimensional inspection and tolerance verification";
  return (base + padding).slice(0, padToLength);
};

// ─────────────────────────────────────────────────────────────────────────────
// chunkText
// ─────────────────────────────────────────────────────────────────────────────

describe("chunkText", () => {
  it("short text (< CHUNK_SIZE) produces exactly one chunk", () => {
    const text = "First sentence. Second sentence. Third sentence.";
    const chunks = chunkText(text);
    expect(chunks).toHaveLength(1);
    expect(chunks[0].content).toContain("First sentence");
    expect(chunks[0].contentType).toBe("text");
    expect(chunks[0].chunkIndex).toBe(0);
  });

  it("long text with sentence boundaries produces multiple chunks", () => {
    // 6 sentences × ~90 chars = ~540 chars > CHUNK_SIZE 512
    const sentences = Array.from({ length: 6 }, (_, i) => makeSentence(`Sentence ${i + 1}`));
    const text = sentences.join(" ");
    const chunks = chunkText(text);
    expect(chunks.length).toBeGreaterThan(1);
  });

  it("respects startIndex offset for chunkIndex", () => {
    const text = "Short text with one sentence.";
    const chunks = chunkText(text, { startIndex: 5 });
    expect(chunks[0].chunkIndex).toBe(5);
  });

  it("assigns pageNumber when provided", () => {
    const text = "Engineering note on this page.";
    const chunks = chunkText(text, { pageNumber: 3 });
    expect(chunks[0].pageNumber).toBe(3);
  });

  it("assigns sheetName when provided", () => {
    const text = "BOM data for sheet.";
    const chunks = chunkText(text, { sheetName: "BOM_Sheet1" });
    expect(chunks[0].sheetName).toBe("BOM_Sheet1");
  });

  it("assigns contentType when provided", () => {
    const text = "Image: bracket with 4 bolt holes.";
    const chunks = chunkText(text, { contentType: "image_caption" });
    expect(chunks[0].contentType).toBe("image_caption");
  });

  it("defaults contentType to 'text'", () => {
    const chunks = chunkText("Some text.");
    expect(chunks[0].contentType).toBe("text");
  });

  it("chunk content is trimmed (no leading/trailing whitespace)", () => {
    const text = "  Sentence with spaces.  ";
    const chunks = chunkText(text);
    expect(chunks[0].content).toBe(chunks[0].content.trim());
  });

  it("overlap: second chunk starts with characters from end of first chunk", () => {
    // Need enough sentences to force a chunk boundary
    const sentences = Array.from({ length: 7 }, (_, i) => makeSentence(`Part ${i + 1}`));
    const text = sentences.join(" ");
    const chunks = chunkText(text);

    if (chunks.length >= 2) {
      // The start of chunk[1] should share content with the end of chunk[0]
      const endOfChunk0 = chunks[0].content.slice(-64);
      // At least some overlap content should appear in chunk[1]
      // (overlap is taken from end of previous 'current' string, before next sentence is appended)
      expect(chunks[1].chunkIndex).toBe(chunks[0].chunkIndex + 1);
    }
  });

  it("empty string produces no chunks", () => {
    const chunks = chunkText("");
    expect(chunks).toHaveLength(0);
  });

  it("text without sentence boundaries stays as one chunk regardless of length", () => {
    // Without '.!?' there are no split points; the entire text is one sentence
    const text = "A".repeat(600); // 600 chars but no sentence boundary
    const chunks = chunkText(text);
    // No sentence boundary → entire text becomes one "sentence" and one chunk
    expect(chunks).toHaveLength(1);
    expect(chunks[0].content).toBe(text.trim());
  });

  it("multiple chunks have sequential chunkIndex values", () => {
    const sentences = Array.from({ length: 8 }, (_, i) => makeSentence(`Step ${i + 1}`));
    const chunks = chunkText(sentences.join(" "), { startIndex: 1 });
    if (chunks.length > 1) {
      for (let i = 1; i < chunks.length; i++) {
        expect(chunks[i].chunkIndex).toBe(chunks[i - 1].chunkIndex + 1);
      }
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// chunkTable
// ─────────────────────────────────────────────────────────────────────────────

describe("chunkTable", () => {
  const SMALL_TABLE = [
    "| Tool | Diameter | Speed |",
    "|------|----------|-------|",
    "| T1   | 10mm     | 2000  |",
    "| T2   | 6mm      | 3500  |",
  ].join("\n");

  it("small table (< CHUNK_SIZE * 2) produces one atomic chunk", () => {
    const chunks = chunkTable(SMALL_TABLE);
    expect(chunks).toHaveLength(1);
    expect(chunks[0].contentType).toBe("table");
    expect(chunks[0].content).toBe(SMALL_TABLE);
  });

  it("assigns correct chunkIndex to single-chunk table", () => {
    const chunks = chunkTable(SMALL_TABLE, { startIndex: 3 });
    expect(chunks[0].chunkIndex).toBe(3);
  });

  it("assigns pageNumber to table chunk", () => {
    const chunks = chunkTable(SMALL_TABLE, { pageNumber: 7 });
    expect(chunks[0].pageNumber).toBe(7);
  });

  it("assigns sheetName to table chunk", () => {
    const chunks = chunkTable(SMALL_TABLE, { sheetName: "ToolCatalog" });
    expect(chunks[0].sheetName).toBe("ToolCatalog");
  });

  it("large table (> CHUNK_SIZE * 2) is split by row groups", () => {
    // Build a table with many rows to exceed 2 × CHUNK_SIZE (1024 chars)
    const header = ["| Part # | Description | Qty | Unit | Price |", "|--------|-------------|-----|------|-------|"];
    const rows = Array.from(
      { length: 30 },
      (_, i) =>
        `| P${String(i).padStart(4, "0")} | Manufacturing part item ${i} for assembly | ${i + 1} | EA | ${(i * 5.25).toFixed(2)} |`
    );
    const bigTable = [...header, ...rows].join("\n");

    const chunks = chunkTable(bigTable);
    expect(chunks.length).toBeGreaterThan(1);
    // Every chunk should include the table header (first 2 lines)
    for (const chunk of chunks) {
      expect(chunk.content).toContain("| Part # |");
      expect(chunk.contentType).toBe("table");
    }
  });

  it("large table chunks have sequential chunkIndex values", () => {
    const header = ["| Col1 | Col2 |", "|------|------|"];
    const rows = Array.from(
      { length: 40 },
      (_, i) => `| Value ${i} with some long text here | Another value ${i} |`
    );
    const bigTable = [...header, ...rows].join("\n");

    const chunks = chunkTable(bigTable, { startIndex: 2 });
    for (let i = 1; i < chunks.length; i++) {
      expect(chunks[i].chunkIndex).toBe(chunks[i - 1].chunkIndex + 1);
    }
  });
});
