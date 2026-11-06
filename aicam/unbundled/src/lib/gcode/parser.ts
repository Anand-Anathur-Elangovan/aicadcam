// Client-side G-Code parser: converts raw G-code text → 3D toolpath segments
// Supports G0/G1 (linear), G2/G3 (circular arc), G20/G21 (unit switching)

export type MoveType = "rapid" | "feed" | "arc_cw" | "arc_ccw";

export interface ToolpathSegment {
  from: [number, number, number];
  to: [number, number, number];
  type: MoveType;
  lineIndex: number;   // source G-code line number (0-based)
  feedRate?: number;
  spindleSpeed?: number;
}

export interface GCodeBlock {
  lineIndex: number;
  raw: string;
  g?: number;          // active G-code (0,1,2,3,17,20,21,90,91…)
  x?: number;
  y?: number;
  z?: number;
  i?: number;          // arc center offset X
  j?: number;          // arc center offset Y
  k?: number;          // arc center offset Z
  r?: number;          // arc radius (alternative to IJK)
  f?: number;          // feed rate
  s?: number;          // spindle speed
  t?: number;          // tool number
  m?: number;          // M-code
}

export interface ParseResult {
  segments: ToolpathSegment[];
  blocks: GCodeBlock[];
  bounds: { min: [number, number, number]; max: [number, number, number] };
  totalLength: number;   // estimated toolpath length in mm
  lineToSegment: Map<number, number>;  // lineIndex → segment index
}

// ─── Main Parser ─────────────────────────────────────────────────────────────

export function parseGCode(text: string): ParseResult {
  const lines = text.split("\n");
  const blocks: GCodeBlock[] = [];
  const segments: ToolpathSegment[] = [];
  const lineToSegment = new Map<number, number>();

  // Machine state
  let curX = 0, curY = 0, curZ = 0;
  let activeG = 0;          // last motion G-code
  let isMetric = true;      // G21=metric, G20=inch
  let isAbsolute = true;    // G90=absolute, G91=incremental
  let curF = 0;
  let curS = 0;

  const toMM = (v: number) => isMetric ? v : v * 25.4;

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i].trim();
    if (!raw || raw.startsWith(";") || raw.startsWith("(") || raw === "%") continue;

    // Strip inline comments
    const cleaned = raw.replace(/\(.*?\)/g, "").replace(/;.*$/, "").trim();
    if (!cleaned) continue;

    const block = parseBlock(cleaned, i);
    if (!block) continue;
    blocks.push(block);

    // Update persistent state
    if (block.f !== undefined) curF = block.f;
    if (block.s !== undefined) curS = block.s;
    if (block.g === 20) isMetric = false;
    if (block.g === 21) isMetric = true;
    if (block.g === 90) isAbsolute = true;
    if (block.g === 91) isAbsolute = false;

    // Determine active motion code
    const motionG = block.g !== undefined && [0, 1, 2, 3].includes(block.g)
      ? block.g
      : activeG;

    // Only process if there's a target position
    const hasPosition = block.x !== undefined || block.y !== undefined || block.z !== undefined;
    if (!hasPosition) {
      if (block.g !== undefined && [0, 1, 2, 3].includes(block.g)) activeG = block.g;
      continue;
    }

    // Resolve target coordinates
    const rawX = block.x !== undefined ? toMM(block.x) : (isAbsolute ? curX : 0);
    const rawY = block.y !== undefined ? toMM(block.y) : (isAbsolute ? curY : 0);
    const rawZ = block.z !== undefined ? toMM(block.z) : (isAbsolute ? curZ : 0);

    const toX = isAbsolute ? rawX : curX + rawX;
    const toY = isAbsolute ? rawY : curY + rawY;
    const toZ = isAbsolute ? rawZ : curZ + rawZ;

    const from: [number, number, number] = [curX, curY, curZ];
    const to:   [number, number, number] = [toX, toY, toZ];

    if (motionG === 0) {
      segments.push({ from, to, type: "rapid", lineIndex: i, feedRate: curF, spindleSpeed: curS });
      lineToSegment.set(i, segments.length - 1);
    } else if (motionG === 1) {
      segments.push({ from, to, type: "feed", lineIndex: i, feedRate: curF, spindleSpeed: curS });
      lineToSegment.set(i, segments.length - 1);
    } else if (motionG === 2 || motionG === 3) {
      // Approximate arc as polyline (16 segments)
      const arcSegs = approximateArc(
        from, to,
        block.i ? toMM(block.i) : 0,
        block.j ? toMM(block.j) : 0,
        block.k ? toMM(block.k) : 0,
        block.r !== undefined ? toMM(block.r) : undefined,
        motionG === 2
      );
      const type: MoveType = motionG === 2 ? "arc_cw" : "arc_ccw";
      arcSegs.forEach((seg, si) => {
        segments.push({ from: seg[0], to: seg[1], type, lineIndex: i, feedRate: curF, spindleSpeed: curS });
        if (si === 0) lineToSegment.set(i, segments.length - 1);
      });
    }

    curX = toX; curY = toY; curZ = toZ;
    if ([0, 1, 2, 3].includes(motionG)) activeG = motionG;
  }

  // Calculate bounding box
  const allPoints = segments.flatMap((s) => [s.from, s.to]);
  const xs = allPoints.map((p) => p[0]);
  const ys = allPoints.map((p) => p[1]);
  const zs = allPoints.map((p) => p[2]);

  const bounds = {
    min: [Math.min(...xs, 0), Math.min(...ys, 0), Math.min(...zs, 0)] as [number, number, number],
    max: [Math.max(...xs, 0), Math.max(...ys, 0), Math.max(...zs, 0)] as [number, number, number],
  };

  // Estimate total path length
  const totalLength = segments.reduce((sum, s) => {
    const dx = s.to[0] - s.from[0];
    const dy = s.to[1] - s.from[1];
    const dz = s.to[2] - s.from[2];
    return sum + Math.sqrt(dx * dx + dy * dy + dz * dz);
  }, 0);

  return { segments, blocks, bounds, totalLength, lineToSegment };
}

// ─── Block Parser ─────────────────────────────────────────────────────────────

function parseBlock(line: string, lineIndex: number): GCodeBlock | null {
  const block: GCodeBlock = { lineIndex, raw: line };
  // Match all word-address pairs: letter + optional sign + number
  const TOKEN = /([A-Z])([-+]?\d*\.?\d+)/gi;
  let match: RegExpExecArray | null;

  while ((match = TOKEN.exec(line)) !== null) {
    const letter = match[1].toUpperCase();
    const value = parseFloat(match[2]);
    switch (letter) {
      case "G": block.g = value; break;
      case "X": block.x = value; break;
      case "Y": block.y = value; break;
      case "Z": block.z = value; break;
      case "I": block.i = value; break;
      case "J": block.j = value; break;
      case "K": block.k = value; break;
      case "R": block.r = value; break;
      case "F": block.f = value; break;
      case "S": block.s = value; break;
      case "T": block.t = value; break;
      case "M": block.m = value; break;
    }
  }
  return Object.keys(block).length > 2 ? block : null;
}

// ─── Arc Approximator ─────────────────────────────────────────────────────────

function approximateArc(
  from: [number, number, number],
  to: [number, number, number],
  I: number, J: number, _K: number,
  R: number | undefined,
  clockwise: boolean,
  steps = 16
): [[number, number, number], [number, number, number]][] {
  // Work in XY plane (G17 default)
  const cx = from[0] + I;
  const cy = from[1] + J;

  let startAngle = Math.atan2(from[1] - cy, from[0] - cx);
  let endAngle   = Math.atan2(to[1]   - cy, to[0]   - cx);
  const radius   = R ?? Math.hypot(from[0] - cx, from[1] - cy);

  if (clockwise  && endAngle > startAngle) endAngle -= 2 * Math.PI;
  if (!clockwise && endAngle < startAngle) endAngle += 2 * Math.PI;

  const result: [[number, number, number], [number, number, number]][] = [];
  const dZ = (to[2] - from[2]) / steps;

  for (let i = 0; i < steps; i++) {
    const t0 = i / steps;
    const t1 = (i + 1) / steps;
    const a0 = startAngle + (endAngle - startAngle) * t0;
    const a1 = startAngle + (endAngle - startAngle) * t1;
    const p0: [number, number, number] = [cx + radius * Math.cos(a0), cy + radius * Math.sin(a0), from[2] + dZ * i];
    const p1: [number, number, number] = [cx + radius * Math.cos(a1), cy + radius * Math.sin(a1), from[2] + dZ * (i + 1)];
    result.push([p0, p1]);
  }
  return result;
}

// ─── Utilities ────────────────────────────────────────────────────────────────

/** Get the tool position for a given line index */
export function getPositionAtLine(result: ParseResult, lineIndex: number): [number, number, number] | null {
  const segIdx = result.lineToSegment.get(lineIndex);
  if (segIdx === undefined) {
    // Search nearest segment
    for (let i = lineIndex; i >= 0; i--) {
      const s = result.lineToSegment.get(i);
      if (s !== undefined) return result.segments[s].to;
    }
    return null;
  }
  return result.segments[segIdx].to;
}

/** Sample CAPP-generated G-code for testing */
export const SAMPLE_GCODE = `%
O0001 (FLANGE BODY - DWG-A901 REV3)
(MATERIAL: ALUMINUM 6061-T6)
(MACHINE: FANUC 3-AXIS VMC)
(DATE: ${new Date().toISOString().slice(0, 10)})
;
G00 G17 G21 G40 G49 G80 G90
G54
;
(OP 10 - FACE MILLING)
T01 M06
G43 H01 Z50.0
S2500 M03
M08
G00 X-5.0 Y-5.0
G00 Z2.0
G01 Z-0.5 F100
G01 X85.0 F300
G01 Y10.0
G01 X-5.0
G01 Y20.0
G01 X85.0
G00 Z50.0
;
(OP 20 - POCKET MILLING)
T02 M06
G43 H02 Z50.0
S3500 M03
G00 X20.0 Y20.0
G00 Z2.0
G01 Z-5.0 F80
G01 X60.0 F200
G01 Y40.0
G01 X20.0
G01 Y20.0
G01 Z-10.0 F80
G01 X60.0 F200
G01 Y40.0
G01 X20.0
G01 Y20.0
G00 Z50.0
;
(OP 30 - DRILLING 4x HOLES)
T03 M06
G43 H03 Z50.0
S1800 M03
G81 Z-15.0 R2.0 F120
X15.0 Y15.0
X65.0 Y15.0
X65.0 Y45.0
X15.0 Y45.0
G80
G00 Z50.0
;
(OP 40 - CIRCULAR PROFILE)
T04 M06
G43 H04 Z50.0
S4000 M03
G00 X40.0 Y30.0
G00 Z2.0
G01 Z-3.0 F80
G02 X40.0 Y30.0 I10.0 J0.0 F250
G00 Z50.0
;
M05
M09
G00 G28 Z0
G00 G28 X0 Y0
M30
%`;
