// G-Code Diff Engine — Myers line-by-line diff algorithm
// Compares AI-generated G-code against human-approved version.
// Produces structured diff for storage, vector embedding, and UI display.

export type DiffType = "equal" | "insert" | "delete";

export interface DiffLine {
  type:       DiffType;
  lineNumber: number;    // line number in the relevant version
  content:    string;    // raw line content
}

export interface GCodeDiff {
  lines:         DiffLine[];
  linesAdded:    number;
  linesRemoved:  number;
  linesUnchanged: number;
  totalGenerated: number;
  totalApproved:  number;
  editSummary:   EditSummary[];
}

export interface EditSummary {
  category:    "feed_rate" | "spindle_speed" | "tool_change" | "coordinates" | "cycle_type" | "coolant" | "retraction" | "other";
  description: string;
  generatedValue?: string;
  approvedValue?:  string;
}

// ─── Myers LCS Diff ──────────────────────────────────────────────────────────

export function diffGCode(generated: string, approved: string): GCodeDiff {
  const genLines = generated.split("\n");
  const appLines = approved.split("\n");

  const diffLines = myersDiff(genLines, appLines);

  let added = 0, removed = 0, unchanged = 0;
  for (const d of diffLines) {
    if (d.type === "insert")  added++;
    else if (d.type === "delete") removed++;
    else unchanged++;
  }

  const editSummary = extractEditSummary(genLines, appLines, diffLines);

  return {
    lines:          diffLines,
    linesAdded:     added,
    linesRemoved:   removed,
    linesUnchanged: unchanged,
    totalGenerated: genLines.length,
    totalApproved:  appLines.length,
    editSummary,
  };
}

function myersDiff(a: string[], b: string[]): DiffLine[] {
  const n = a.length;
  const m = b.length;
  const max = n + m;

  if (max === 0) return [];

  // V array: V[k] = furthest x reached on diagonal k
  const v: number[] = new Array(2 * max + 1).fill(0);
  const trace: number[][] = [];

  outer: for (let d = 0; d <= max; d++) {
    trace.push([...v]);
    for (let k = -d; k <= d; k += 2) {
      const ki = k + max;
      let x: number;

      if (k === -d || (k !== d && v[ki - 1] < v[ki + 1])) {
        x = v[ki + 1];
      } else {
        x = v[ki - 1] + 1;
      }

      let y = x - k;

      while (x < n && y < m && a[x] === b[y]) {
        x++;
        y++;
      }

      v[ki] = x;

      if (x >= n && y >= m) {
        break outer;
      }
    }
  }

  return backtrack(a, b, trace, max);
}

function backtrack(a: string[], b: string[], trace: number[][], max: number): DiffLine[] {
  let x = a.length;
  let y = b.length;
  const result: DiffLine[] = [];

  for (let d = trace.length - 1; d >= 0; d--) {
    const v   = trace[d];
    const k   = x - y;
    const ki  = k + max;

    let prevK: number;
    if (k === -d || (k !== d && v[ki - 1] < v[ki + 1])) {
      prevK = k + 1;
    } else {
      prevK = k - 1;
    }

    const prevX = v[prevK + max];
    const prevY = prevX - prevK;

    while (x > prevX && y > prevY) {
      result.unshift({ type: "equal",  lineNumber: x - 1, content: a[x - 1] });
      x--; y--;
    }

    if (d > 0) {
      if (x === prevX) {
        result.unshift({ type: "insert", lineNumber: y - 1, content: b[y - 1] });
        y--;
      } else {
        result.unshift({ type: "delete", lineNumber: x - 1, content: a[x - 1] });
        x--;
      }
    }
  }

  return result;
}

// ─── Semantic Edit Extraction ─────────────────────────────────────────────────

const FEED_RE    = /\bF([\d.]+)/i;
const SPINDLE_RE = /\bS([\d.]+)/i;
const TOOL_RE    = /\bT(\d+)/i;
const CYCLE_RE   = /\b(G8[0-9]|G73|G74|G76|G83|G84|G85|G86)\b/i;
const COOLANT_RE = /\bM0?[89]\b/i;
const ZRETRACT_RE = /\bG00?\s.*Z([\d.-]+)/i;

function extractEditSummary(
  genLines: string[],
  appLines: string[],
  diffs: DiffLine[]
): EditSummary[] {
  const summaries: EditSummary[] = [];

  const deletedLines = diffs.filter((d) => d.type === "delete").map((d) => d.content);
  const insertedLines = diffs.filter((d) => d.type === "insert").map((d) => d.content);

  const genText = deletedLines.join("\n");
  const appText = insertedLines.join("\n");

  // Feed rate changes
  const genFeed = FEED_RE.exec(genText)?.[1];
  const appFeed = FEED_RE.exec(appText)?.[1];
  if (genFeed && appFeed && genFeed !== appFeed) {
    summaries.push({
      category: "feed_rate",
      description: `Feed rate changed from F${genFeed} to F${appFeed}`,
      generatedValue: `F${genFeed}`,
      approvedValue:  `F${appFeed}`,
    });
  }

  // Spindle speed changes
  const genSpin = SPINDLE_RE.exec(genText)?.[1];
  const appSpin = SPINDLE_RE.exec(appText)?.[1];
  if (genSpin && appSpin && genSpin !== appSpin) {
    summaries.push({
      category: "spindle_speed",
      description: `Spindle speed changed from S${genSpin} to S${appSpin}`,
      generatedValue: `S${genSpin}`,
      approvedValue:  `S${appSpin}`,
    });
  }

  // Tool changes
  const genTool = TOOL_RE.exec(genText)?.[1];
  const appTool = TOOL_RE.exec(appText)?.[1];
  if (genTool && appTool && genTool !== appTool) {
    summaries.push({
      category: "tool_change",
      description: `Tool changed from T${genTool} to T${appTool}`,
      generatedValue: `T${genTool}`,
      approvedValue:  `T${appTool}`,
    });
  }

  // Cycle type changes
  const genCycle = CYCLE_RE.exec(genText)?.[1];
  const appCycle = CYCLE_RE.exec(appText)?.[1];
  if (genCycle !== appCycle && (genCycle || appCycle)) {
    summaries.push({
      category: "cycle_type",
      description: `Cycle changed: ${genCycle ?? "none"} → ${appCycle ?? "none"}`,
      generatedValue: genCycle,
      approvedValue:  appCycle,
    });
  }

  // Retraction height changes
  const genZ = ZRETRACT_RE.exec(genText)?.[1];
  const appZ = ZRETRACT_RE.exec(appText)?.[1];
  if (genZ && appZ && genZ !== appZ) {
    summaries.push({
      category: "retraction",
      description: `Z retraction changed from Z${genZ} to Z${appZ}`,
      generatedValue: `Z${genZ}`,
      approvedValue:  `Z${appZ}`,
    });
  }

  // Generic: lines added with no matched category
  if (insertedLines.length > 0 && summaries.length === 0) {
    summaries.push({
      category: "other",
      description: `${insertedLines.length} line(s) added, ${deletedLines.length} line(s) removed`,
    });
  }

  return summaries;
}

// ─── Geometry Hash ────────────────────────────────────────────────────────────

import { createHash } from "crypto";

function sortedStringify(value: unknown): string {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return JSON.stringify(value);
  }
  const obj = value as Record<string, unknown>;
  const sorted = Object.keys(obj).sort().map((k) => `${JSON.stringify(k)}:${sortedStringify(obj[k])}`);
  return `{${sorted.join(",")}}`;
}

export function hashPartFeatures(features: Record<string, unknown>): string {
  const stable = sortedStringify(features);
  return createHash("sha256").update(stable).digest("hex").slice(0, 16);
}

// ─── Few-Shot Prompt Builder ──────────────────────────────────────────────────

export interface FewShotExample {
  partContext:   string;
  editSummary:   EditSummary[];
  editReason?:   string;
  approvedGcode: string;
}

export function buildFewShotRules(examples: FewShotExample[]): string {
  if (!examples.length) return "";

  const rules = examples.map((ex, i) => {
    const summaryText = ex.editSummary
      .map((s) => `- ${s.description}`)
      .join("\n");
    return `[PAST CORRECTION ${i + 1}]
Part Context: ${ex.partContext}
Engineer Edits:
${summaryText}${ex.editReason ? `\nReason: ${ex.editReason}` : ""}`;
  });

  return `\n\n[ENGINEERING PREFERENCES FROM PAST APPROVALS]\nApply the following learned rules from past human corrections:\n\n${rules.join("\n\n")}`;
}
