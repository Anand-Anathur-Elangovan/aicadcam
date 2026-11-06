import { describe, it, expect } from "vitest";
import {
  diffGCode,
  hashPartFeatures,
  buildFewShotRules,
} from "@/lib/feedback/diffEngine";

// ─────────────────────────────────────────────────────────────────────────────
// diffGCode — Myers line-by-line diff
// ─────────────────────────────────────────────────────────────────────────────

describe("diffGCode", () => {
  it("identical code produces zero changes", () => {
    const code = "G21\nG90\nM6 T1\nS1000 M3";
    const result = diffGCode(code, code);
    expect(result.linesAdded).toBe(0);
    expect(result.linesRemoved).toBe(0);
    expect(result.linesUnchanged).toBe(4);
    expect(result.editSummary).toHaveLength(0);
    expect(result.totalGenerated).toBe(4);
    expect(result.totalApproved).toBe(4);
  });

  it("detects a single inserted line", () => {
    const generated = "G21\nG90\nM6 T1";
    const approved = "G21\nG90\nM08\nM6 T1";
    const result = diffGCode(generated, approved);
    expect(result.linesAdded).toBe(1);
    expect(result.linesRemoved).toBe(0);
    expect(result.linesUnchanged).toBe(3);
  });

  it("detects a single deleted line", () => {
    const generated = "G21\nG90\nM08\nM6 T1";
    const approved = "G21\nG90\nM6 T1";
    const result = diffGCode(generated, approved);
    expect(result.linesAdded).toBe(0);
    expect(result.linesRemoved).toBe(1);
    expect(result.linesUnchanged).toBe(3);
  });

  it("handles empty string inputs (edge case)", () => {
    // "".split("\n") = [""], so each has 1 empty line — treated as equal
    const result = diffGCode("", "");
    expect(result.linesAdded).toBe(0);
    expect(result.linesRemoved).toBe(0);
    expect(result.totalGenerated).toBe(1);
    expect(result.totalApproved).toBe(1);
  });

  it("handles complete rewrite — all lines differ", () => {
    const generated = "G21\nG90";
    const approved = "G20\nG91";
    const result = diffGCode(generated, approved);
    expect(result.linesRemoved).toBe(2);
    expect(result.linesAdded).toBe(2);
    expect(result.linesUnchanged).toBe(0);
  });

  it("totalGenerated and totalApproved reflect actual line counts", () => {
    const result = diffGCode("A\nB\nC", "A\nD\nC\nE");
    expect(result.totalGenerated).toBe(3);
    expect(result.totalApproved).toBe(4);
  });

  // ── Semantic edit summaries ──────────────────────────────────────────────

  it("detects feed rate change (F100 → F250)", () => {
    const generated = "G01 X10.0 Y5.0 F100";
    const approved = "G01 X10.0 Y5.0 F250";
    const result = diffGCode(generated, approved);
    const feed = result.editSummary.find((s) => s.category === "feed_rate");
    expect(feed).toBeDefined();
    expect(feed?.generatedValue).toBe("F100");
    expect(feed?.approvedValue).toBe("F250");
    expect(feed?.description).toContain("F100");
    expect(feed?.description).toContain("F250");
  });

  it("detects spindle speed change (S800 → S2500)", () => {
    const generated = "G97 S800 M03";
    const approved = "G97 S2500 M03";
    const result = diffGCode(generated, approved);
    const spin = result.editSummary.find((s) => s.category === "spindle_speed");
    expect(spin).toBeDefined();
    expect(spin?.generatedValue).toBe("S800");
    expect(spin?.approvedValue).toBe("S2500");
  });

  it("detects tool change (T2 → T5)", () => {
    const generated = "M6 T2";
    const approved = "M6 T5";
    const result = diffGCode(generated, approved);
    const tool = result.editSummary.find((s) => s.category === "tool_change");
    expect(tool).toBeDefined();
    expect(tool?.generatedValue).toBe("T2");
    expect(tool?.approvedValue).toBe("T5");
  });

  it("detects canned cycle type change (G81 → G83 peck drill)", () => {
    const generated = "G81 X0 Y0 Z-15 R2 F80";
    const approved = "G83 X0 Y0 Z-15 Q3 R2 F80";
    const result = diffGCode(generated, approved);
    const cycle = result.editSummary.find((s) => s.category === "cycle_type");
    expect(cycle).toBeDefined();
    expect(cycle?.generatedValue?.toUpperCase()).toBe("G81");
    expect(cycle?.approvedValue?.toUpperCase()).toBe("G83");
  });

  it("detects Z retraction height change", () => {
    const generated = "G00 X0 Y0 Z2.0";
    const approved = "G00 X0 Y0 Z5.0";
    const result = diffGCode(generated, approved);
    const retract = result.editSummary.find((s) => s.category === "retraction");
    expect(retract).toBeDefined();
    expect(retract?.generatedValue).toBe("Z2.0");
    expect(retract?.approvedValue).toBe("Z5.0");
  });

  it("falls back to 'other' when no semantic category matches", () => {
    const generated = "( no semantic change )";
    const approved = "( different comment )";
    const result = diffGCode(generated, approved);
    expect(result.editSummary.length).toBeGreaterThan(0);
    const other = result.editSummary.find((s) => s.category === "other");
    expect(other).toBeDefined();
  });

  it("realistic full G-code snippet — spindle + feed changed", () => {
    const generated = [
      "%",
      "O0001 (BRACKET FACE)",
      "G21 G90 G17",
      "T1 M6",
      "G0 X0 Y0",
      "G43 H1 Z50",
      "S1000 M3",
      "G1 Z-5 F100",
      "G1 X50 Y50 F200",
      "G0 Z50",
      "M30",
      "%",
    ].join("\n");

    const approved = [
      "%",
      "O0001 (BRACKET FACE)",
      "G21 G90 G17",
      "T1 M6",
      "G0 X0 Y0",
      "G43 H1 Z50",
      "S2500 M3",           // spindle changed
      "G1 Z-5 F100",
      "G1 X50 Y50 F350",    // feed changed
      "G0 Z50",
      "M30",
      "%",
    ].join("\n");

    const result = diffGCode(generated, approved);
    expect(result.linesUnchanged).toBe(10);
    expect(result.linesRemoved).toBe(2);
    expect(result.linesAdded).toBe(2);

    const spin = result.editSummary.find((s) => s.category === "spindle_speed");
    expect(spin?.generatedValue).toBe("S1000");
    expect(spin?.approvedValue).toBe("S2500");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// hashPartFeatures — SHA-256 geometry hash
// ─────────────────────────────────────────────────────────────────────────────

describe("hashPartFeatures", () => {
  it("returns consistent hash for same input", () => {
    const features = { material: "aluminum", diameter: 25, tolerance: 0.01 };
    expect(hashPartFeatures(features)).toBe(hashPartFeatures(features));
  });

  it("produces same hash regardless of key insertion order", () => {
    const a = { material: "aluminum", diameter: 25 };
    const b = { diameter: 25, material: "aluminum" };
    expect(hashPartFeatures(a)).toBe(hashPartFeatures(b));
  });

  it("produces different hashes for different values", () => {
    const a = { material: "aluminum", diameter: 25 };
    const b = { material: "steel", diameter: 25 };
    expect(hashPartFeatures(a)).not.toBe(hashPartFeatures(b));
  });

  it("returns a 16-character lowercase hex string", () => {
    const result = hashPartFeatures({ key: "value" });
    expect(result).toHaveLength(16);
    expect(result).toMatch(/^[0-9a-f]+$/);
  });

  it("handles empty object", () => {
    const result = hashPartFeatures({});
    expect(result).toHaveLength(16);
  });

  it("nested values are included in hash", () => {
    const a = { dims: { x: 10, y: 20 } };
    const b = { dims: { x: 10, y: 99 } };
    expect(hashPartFeatures(a)).not.toBe(hashPartFeatures(b));
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// buildFewShotRules — prompt injection from feedback memory
// ─────────────────────────────────────────────────────────────────────────────

describe("buildFewShotRules", () => {
  it("returns empty string for empty examples array", () => {
    expect(buildFewShotRules([])).toBe("");
  });

  it("includes numbered past correction headers", () => {
    const examples = [
      {
        partContext: "Aluminum 6061 bracket",
        editSummary: [
          {
            category: "feed_rate" as const,
            description: "Feed rate changed from F100 to F200",
            generatedValue: "F100",
            approvedValue: "F200",
          },
        ],
        approvedGcode: "G01 X10 F200",
      },
    ];
    const result = buildFewShotRules(examples);
    expect(result).toContain("PAST CORRECTION 1");
    expect(result).toContain("Aluminum 6061 bracket");
    expect(result).toContain("Feed rate changed from F100 to F200");
  });

  it("includes edit reason when provided", () => {
    const examples = [
      {
        partContext: "Hardened steel shaft",
        editSummary: [
          {
            category: "spindle_speed" as const,
            description: "Spindle speed reduced due to material hardness",
          },
        ],
        editReason: "Part material requires lower RPM to prevent tool breakage",
        approvedGcode: "G97 S800 M03",
      },
    ];
    const result = buildFewShotRules(examples);
    expect(result).toContain("Part material requires lower RPM");
  });

  it("omits Reason line when editReason is not provided", () => {
    const examples = [
      {
        partContext: "Mild steel block",
        editSummary: [{ category: "other" as const, description: "Added coolant" }],
        approvedGcode: "M08",
      },
    ];
    const result = buildFewShotRules(examples);
    expect(result).not.toContain("Reason:");
  });

  it("numbers multiple examples sequentially", () => {
    const examples = [
      {
        partContext: "Part A",
        editSummary: [{ category: "feed_rate" as const, description: "F100 → F200" }],
        approvedGcode: "G1 F200",
      },
      {
        partContext: "Part B",
        editSummary: [{ category: "tool_change" as const, description: "T1 → T3" }],
        approvedGcode: "M6 T3",
      },
    ];
    const result = buildFewShotRules(examples);
    expect(result).toContain("PAST CORRECTION 1");
    expect(result).toContain("PAST CORRECTION 2");
    expect(result).toContain("Part A");
    expect(result).toContain("Part B");
  });
});
