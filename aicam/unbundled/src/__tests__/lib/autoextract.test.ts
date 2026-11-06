import { describe, it, expect } from "vitest";
import { autoExtractFromText, buildMetadataHeaderChunk } from "@/lib/rag/autoextract";

// ─────────────────────────────────────────────────────────────────────────────
// autoExtractFromText — regex-based extraction from document text
// ─────────────────────────────────────────────────────────────────────────────

describe("autoExtractFromText — part number extraction", () => {
  it("extracts part number in ALPHA-NUMERIC format (e.g. DWG-A901)", () => {
    const text = "This drawing is for part DWG-A901 used in assembly.";
    const result = autoExtractFromText(text, {});
    expect(result.partNumber).toBe("DWG-A901");
  });

  it("extracts part number with P/N prefix", () => {
    const text = "P/N: XYZ-001-A is the main assembly part.";
    const result = autoExtractFromText(text, {});
    expect(result.partNumber).toBe("XYZ-001-A");
  });

  it("extracts part number with 'Part No:' prefix", () => {
    const text = "Part No: ABC-9876 is referenced in this document.";
    const result = autoExtractFromText(text, {});
    expect(result.partNumber).toBe("ABC-9876");
  });

  it("extracts part number with 'PN:' prefix", () => {
    const text = "PN: BOLT-M8X20 is included in the BOM.";
    const result = autoExtractFromText(text, {});
    expect(result.partNumber).toBe("BOLT-M8X20");
  });

  it("does NOT extract partNumber when manually provided", () => {
    const text = "Part ABC-99999 mentioned here.";
    const result = autoExtractFromText(text, { partNumber: "MANUAL-001" });
    // existing.partNumber is set, so auto-extract should skip it
    expect(result.partNumber).toBeUndefined();
  });

  it("returns empty object when no part number pattern matches", () => {
    const text = "No part or serial number in this text.";
    const result = autoExtractFromText(text, {});
    expect(result.partNumber).toBeUndefined();
  });
});

describe("autoExtractFromText — revision extraction", () => {
  it("extracts revision from 'Rev. A' format", () => {
    const text = "This drawing is at Rev. A dated 2024.";
    const result = autoExtractFromText(text, {});
    expect(result.revision).toBe("A");
  });

  it("extracts revision from 'Revision 3' format", () => {
    const text = "Document Revision 3 approved by chief engineer.";
    const result = autoExtractFromText(text, {});
    expect(result.revision).toBe("3");
  });

  it("extracts revision from 'REV3' format", () => {
    const text = "Drawing REV3 is current.";
    const result = autoExtractFromText(text, {});
    expect(result.revision).toBe("3");
  });

  it("extracts revision from inline '-R02' format", () => {
    const text = "File: DWG-BRACKET-R02.pdf was approved.";
    const result = autoExtractFromText(text, {});
    expect(result.revision).toBe("02");
  });

  it("does NOT extract revision when manually provided", () => {
    const text = "Rev. B mentioned in document.";
    const result = autoExtractFromText(text, { revision: "A" });
    expect(result.revision).toBeUndefined();
  });

  it("normalizes revision to uppercase", () => {
    const text = "Rev. c approved.";
    const result = autoExtractFromText(text, {});
    expect(result.revision).toBe("C");
  });
});

describe("autoExtractFromText — stateful regex bug", () => {
  // BUG: The module-level regexes use the 'g' flag with exec().
  // After a successful match, lastIndex is advanced. Subsequent calls
  // where the match position is BEFORE the stale lastIndex miss the match.

  it("correctly extracts from two consecutive calls with different text positions", () => {
    // Call 1: match is near the END of the string (advances lastIndex far)
    const textA = "Long text with many words here then DWG-A001 at position 36";
    const resultA = autoExtractFromText(textA, {});
    expect(resultA.partNumber).toBe("DWG-A001");

    // Call 2: match is at the START of the string (before stale lastIndex)
    // This FAILS with the bug — the regex starts from old lastIndex, misses DWG-B002
    const textB = "DWG-B002 is referenced in the BOM.";
    const resultB = autoExtractFromText(textB, {});
    expect(resultB.partNumber).toBe("DWG-B002"); // FAILS if regex 'g' flag bug is present
  });

  it("extracts correctly on every call regardless of prior call history", () => {
    const texts = [
      { text: "ABCD-123 at end of string pad pad pad", expected: "ABCD-123" },
      { text: "EFGH-456 is at start", expected: "EFGH-456" },
      { text: "middle IJKL-789 appears here", expected: "IJKL-789" },
    ];

    for (const { text, expected } of texts) {
      const result = autoExtractFromText(text, {});
      expect(result.partNumber, `Failed for text: "${text}"`).toBe(expected);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// buildMetadataHeaderChunk — generates the chunk_index=0 metadata header
// ─────────────────────────────────────────────────────────────────────────────

describe("buildMetadataHeaderChunk", () => {
  it("always includes DOCUMENT and CATEGORY lines", () => {
    const result = buildMetadataHeaderChunk({}, {}, "DRAWING_2D", "Bracket-Rev-A.pdf");
    expect(result).toContain("DOCUMENT: Bracket-Rev-A.pdf");
    expect(result).toContain("CATEGORY: DRAWING_2D");
  });

  it("includes all provided user fields", () => {
    const fields = {
      docTitle: "Bracket Assembly Drawing",
      partName: "Main Bracket",
      partNumber: "BRK-001",
      revision: "C",
      customerName: "Acme Corp",
      description: "Front-face bracket for unit 5",
      issueDetails: "Tolerance tightened per ECR-221",
      developedDate: "2024-01-15",
    };
    const result = buildMetadataHeaderChunk(fields, {}, "DRAWING_2D", "test.pdf");

    expect(result).toContain("TITLE: Bracket Assembly Drawing");
    expect(result).toContain("PART NAME: Main Bracket");
    expect(result).toContain("PART NUMBER: BRK-001");
    expect(result).toContain("REVISION: C");
    expect(result).toContain("CUSTOMER: Acme Corp");
    expect(result).toContain("DESCRIPTION: Front-face bracket for unit 5");
    expect(result).toContain("ISSUE DETAILS: Tolerance tightened per ECR-221");
    expect(result).toContain("DEVELOPED DATE: 2024-01-15");
  });

  it("user fields take priority over auto-extracted when both provided", () => {
    const fields = { partNumber: "USER-001" };
    const autoExtracted = { partNumber: "AUTO-999", revision: "B" };
    const result = buildMetadataHeaderChunk(fields, autoExtracted, "GENERAL", "file.pdf");

    expect(result).toContain("PART NUMBER: USER-001"); // user wins
    expect(result).not.toContain("PART NUMBER: AUTO-999");
    expect(result).toContain("REVISION: B"); // auto fills in where user left blank
  });

  it("falls back to auto-extracted when user field is missing", () => {
    const fields = {};
    const autoExtracted = { partNumber: "AUTO-555", revision: "D" };
    const result = buildMetadataHeaderChunk(fields, autoExtracted, "INSPECTION_FIR", "report.pdf");

    expect(result).toContain("PART NUMBER: AUTO-555");
    expect(result).toContain("REVISION: D");
  });

  it("omits lines for empty or missing fields — keeps header minimal", () => {
    const result = buildMetadataHeaderChunk({}, {}, "GENERAL", "empty.pdf");
    expect(result).not.toContain("TITLE:");
    expect(result).not.toContain("PART NUMBER:");
    expect(result).not.toContain("REVISION:");
    expect(result).not.toContain("CUSTOMER:");
    // Only DOCUMENT and CATEGORY should be present
    const lines = result.split("\n").filter(Boolean);
    expect(lines).toHaveLength(2);
  });

  it("output is plain text suitable for embedding (no JSON or markdown)", () => {
    const result = buildMetadataHeaderChunk(
      { partNumber: "ABC-001", revision: "A" },
      {},
      "DRAWING_2D",
      "drawing.pdf"
    );
    expect(result).not.toContain("{");
    expect(result).not.toContain("[");
    expect(result).not.toContain("**");
  });
});
