// Feature extraction from STEP/IGES CAD files and 2D PDF drawings
// Produces a normalized PartFeatures object consumed by the CAPP provider

export interface PartDimensions {
  length: number;  // mm
  width: number;
  height: number;
  diameter?: number;  // for rotational parts
}

export interface MachiningFeature {
  type:
    | "pocket"
    | "hole"
    | "slot"
    | "boss"
    | "thread"
    | "chamfer"
    | "fillet"
    | "profile"
    | "face"
    | "bore"
    | "counterbore"
    | "countersink";
  count: number;
  depth?: number;       // mm
  diameter?: number;    // mm
  width?: number;       // mm
  tolerance?: string;   // e.g. "H7", "±0.05"
  notes?: string;
}

export interface ToleranceSpec {
  feature: string;
  nominal: number;
  tolerance: string;    // e.g. "±0.025", "0/-0.05"
  qualifier?: string;   // "tight" | "standard" | "loose"
}

export interface SurfaceFinish {
  feature: string;
  ra: number;           // Ra in µm
}

export interface PartFeatures {
  partName: string;
  partNumber: string;
  revision: string;
  material: string;
  rawDimensions: PartDimensions;
  stockDimensions: PartDimensions;  // with stock allowance
  features: MachiningFeature[];
  tolerances: ToleranceSpec[];
  surfaceFinishes: SurfaceFinish[];
  partType: "prismatic" | "rotational" | "sheet" | "complex";
  estimatedComplexity: "low" | "medium" | "high";
  sourceFile: string;
  extractedAt: string;
  extractionMethod: "step_parse" | "pdf_ocr" | "manual" | "ai_vision";
}

// ─── PDF / Text Extraction ────────────────────────────────────────────────────

const DIMENSION_RE = /(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)/;
const DIAMETER_RE  = /[Øø∅ΦD]\s*(\d+(?:\.\d+)?)/g;
const TOLERANCE_RE = /[±+\-]?\s*0\.\d+\s*(?:mm)?/g;
const THREAD_RE    = /M(\d+(?:\.\d+)?)\s*[xX×]?\s*(\d+(?:\.\d+)?)?/gi;
const HOLE_RE      = /(\d+)[xX×\s]+holes?\b/gi;
const POCKET_RE    = /pocket|slot|groove|channel/gi;
const ROUGHNESS_RE = /Ra\s*([\d.]+)/gi;
const MATERIAL_RE  = /(?:material|mat\.?)[\s:]+([A-Z0-9 \-]+(?:6061|7075|4140|303|304|316|1018|H13|P20)?)/i;
const PARTNO_RE    = /(?:part\s*(?:no|number|#)|p\/n|dwg)[\s:.-]+([A-Z0-9\-]+)/i;
const REVISION_RE  = /(?:rev(?:ision)?|rev\.)[\s:]+([A-Z0-9]+)/i;

export function extractFeaturesFromText(
  text: string,
  filename: string,
  metaOverrides: Partial<Pick<PartFeatures, "partName" | "partNumber" | "revision" | "material">> = {}
): PartFeatures {
  const lines = text.split("\n");

  // Dimensions
  const dimMatch = DIMENSION_RE.exec(text);
  const rawDim: PartDimensions = dimMatch
    ? { length: parseFloat(dimMatch[1]), width: parseFloat(dimMatch[2]), height: parseFloat(dimMatch[3]) }
    : { length: 100, width: 80, height: 30 };

  // Diameter (rotational parts)
  const diameters: number[] = [];
  let dm: RegExpExecArray | null;
  const tmpDia = new RegExp(DIAMETER_RE.source, "g");
  while ((dm = tmpDia.exec(text)) !== null) diameters.push(parseFloat(dm[1]));
  if (diameters.length) rawDim.diameter = Math.max(...diameters);

  const isRotational = diameters.length > 0 && !dimMatch;

  // Stock: add 3mm all-round machining allowance
  const stockDim: PartDimensions = {
    length: rawDim.length + 6,
    width: rawDim.width + 6,
    height: rawDim.height + 6,
    diameter: rawDim.diameter ? rawDim.diameter + 6 : undefined,
  };

  // Features
  const features: MachiningFeature[] = [];

  // Holes
  const holeTmp = new RegExp(HOLE_RE.source, "gi");
  let hm: RegExpExecArray | null;
  while ((hm = holeTmp.exec(text)) !== null) {
    features.push({ type: "hole", count: parseInt(hm[1]) });
  }

  // Threads
  const threadTmp = new RegExp(THREAD_RE.source, "gi");
  let tm: RegExpExecArray | null;
  while ((tm = threadTmp.exec(text)) !== null) {
    features.push({ type: "thread", count: 1, diameter: parseFloat(tm[1]), notes: tm[0] });
  }

  // Pockets/slots
  const pocketTmp = new RegExp(POCKET_RE.source, "gi");
  let pm: RegExpExecArray | null;
  let pocketCount = 0;
  while ((pm = pocketTmp.exec(text)) !== null) pocketCount++;
  if (pocketCount > 0) features.push({ type: "pocket", count: pocketCount });

  // Tolerances
  const tolerances: ToleranceSpec[] = [];
  const tolTmp = new RegExp(TOLERANCE_RE.source, "g");
  let tlm: RegExpExecArray | null;
  let tolIdx = 0;
  while ((tlm = tolTmp.exec(text)) !== null && tolIdx < 8) {
    tolerances.push({ feature: `feature_${tolIdx++}`, nominal: 0, tolerance: tlm[0].trim() });
  }

  // Surface finishes
  const finishes: SurfaceFinish[] = [];
  const roughTmp = new RegExp(ROUGHNESS_RE.source, "gi");
  let rm: RegExpExecArray | null;
  while ((rm = roughTmp.exec(text)) !== null) {
    finishes.push({ feature: "general", ra: parseFloat(rm[1]) });
  }

  // Metadata
  const partNoMatch  = PARTNO_RE.exec(text);
  const revMatch     = REVISION_RE.exec(text);
  const matMatch     = MATERIAL_RE.exec(text);

  const holeCount   = features.filter((f) => f.type === "hole").length;
  const featureCount = features.length;
  const complexity: PartFeatures["estimatedComplexity"] =
    featureCount > 8 || tolerances.length > 5 ? "high"
    : featureCount > 3 || tolerances.length > 2 ? "medium"
    : "low";

  return {
    partName:    metaOverrides.partName    ?? filename.replace(/\.[^.]+$/, ""),
    partNumber:  metaOverrides.partNumber  ?? partNoMatch?.[1] ?? "UNKNOWN",
    revision:    metaOverrides.revision    ?? revMatch?.[1] ?? "A",
    material:    metaOverrides.material    ?? matMatch?.[1]?.trim() ?? "UNKNOWN",
    rawDimensions:  rawDim,
    stockDimensions: stockDim,
    features,
    tolerances,
    surfaceFinishes: finishes,
    partType: isRotational ? "rotational" : "prismatic",
    estimatedComplexity: complexity,
    sourceFile: filename,
    extractedAt: new Date().toISOString(),
    extractionMethod: "pdf_ocr",
  };
}

// ─── STEP File Extraction (header-only, no full geometry parse) ───────────────

export function extractFeaturesFromStep(stepContent: string, filename: string): PartFeatures {
  // STEP AP214/AP242 header parsing — extracts PRODUCT entity name and description
  const productMatch = /PRODUCT\('([^']+)','([^']+)'/.exec(stepContent);
  const unitMatch    = /CONVERSION_BASED_UNIT.*?'(MILLIMETRE|INCH)'/i.exec(stepContent);
  const isInch       = unitMatch?.[1]?.toUpperCase() === "INCH";
  const scale        = isInch ? 25.4 : 1;

  // Count advanced_face and edge_loop entities as proxy for complexity
  const faceCount    = (stepContent.match(/ADVANCED_FACE/gi) ?? []).length;
  const edgeCount    = (stepContent.match(/EDGE_LOOP/gi) ?? []).length;

  const complexity: PartFeatures["estimatedComplexity"] =
    faceCount > 40 ? "high" : faceCount > 15 ? "medium" : "low";

  const rawDim: PartDimensions = { length: 100 * scale, width: 80 * scale, height: 30 * scale };
  const stockDim: PartDimensions = {
    length: rawDim.length + 6,
    width: rawDim.width + 6,
    height: rawDim.height + 6,
  };

  return {
    partName:    productMatch?.[1] ?? filename.replace(/\.[^.]+$/, ""),
    partNumber:  productMatch?.[2] ?? "UNKNOWN",
    revision:    "A",
    material:    "UNKNOWN",
    rawDimensions:  rawDim,
    stockDimensions: stockDim,
    features:    faceCount > 0 ? [{ type: "face", count: faceCount }] : [],
    tolerances:  [],
    surfaceFinishes: [],
    partType:    "complex",
    estimatedComplexity: complexity,
    sourceFile:  filename,
    extractedAt: new Date().toISOString(),
    extractionMethod: "step_parse",
  };
}

// ─── Router ──────────────────────────────────────────────────────────────────

export function extractFeatures(
  buffer: Buffer,
  mimeType: string,
  filename: string,
  textContent?: string,
  metaOverrides?: Partial<Pick<PartFeatures, "partName" | "partNumber" | "revision" | "material">>
): PartFeatures {
  const lowerName = filename.toLowerCase();

  if (lowerName.endsWith(".stp") || lowerName.endsWith(".step") || lowerName.endsWith(".iges") || lowerName.endsWith(".igs")) {
    return extractFeaturesFromStep(buffer.toString("utf-8", 0, 50_000), filename);
  }

  // For PDFs and other docs, use pre-parsed text content
  const text = textContent ?? buffer.toString("utf-8", 0, 50_000);
  return extractFeaturesFromText(text, filename, metaOverrides);
}
