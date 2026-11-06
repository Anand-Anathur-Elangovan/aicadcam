// CAPP (Computer-Aided Process Planning) Provider
// Dual-mode: Cloud (Anthropic/Gemini JSON schema output) vs On-Prem (Ollama with strict JSON prompts)

import type { PartFeatures } from "../cad/featureExtractor";

// ─── Output Types ─────────────────────────────────────────────────────────────

export type OperationType =
  | "face_milling"
  | "pocket_milling"
  | "profile_milling"
  | "drilling"
  | "tapping"
  | "boring"
  | "reaming"
  | "turning"
  | "threading"
  | "grinding"
  | "deburring"
  | "inspection";

export interface ToolSpec {
  type: string;            // "End Mill", "Drill", "Face Mill", etc.
  diameter: number;        // mm
  material: string;        // "Carbide", "HSS", "CBN"
  coated: boolean;
  numberOfFlutes?: number;
  notes?: string;
}

export interface CuttingParams {
  spindleSpeed: number;    // RPM
  feedRate: number;        // mm/min
  depthOfCut: number;      // mm (axial)
  stepOver?: number;       // mm (radial, for milling)
  coolant: "flood" | "mist" | "air" | "none";
}

export interface SetupInstruction {
  clampingMethod: string;   // "Vice", "Fixture", "Chuck"
  datumFace: string;        // "Bottom face", "Bore center"
  notes?: string;
}

export interface ProcessOperation {
  opNumber: number;         // 10, 20, 30 ...
  name: string;             // "OP10 - Face Milling"
  type: OperationType;
  description: string;
  tool: ToolSpec;
  cuttingParams: CuttingParams;
  setup: SetupInstruction;
  estimatedTime: number;    // minutes
  qualityChecks: string[];
  gcodeSectionHint?: string;  // used by G-code generator
}

export interface ProcessPlan {
  partName: string;
  partNumber: string;
  revision: string;
  material: string;
  machineType: string;       // "3-Axis VMC", "CNC Lathe", "5-Axis VMC"
  totalEstimatedTime: number;  // minutes
  operations: ProcessOperation[];
  generalNotes: string[];
  generatedAt: string;
  model: string;
}

// ─── Prompt Construction ──────────────────────────────────────────────────────

function buildSystemPrompt(): string {
  return `You are an expert CNC process planner (CAPP engineer) with 20+ years of experience in aerospace and automotive manufacturing.
Given a part's features, dimensions, and material, you generate detailed machining process plans.

IMPORTANT: You MUST respond with valid JSON only. No markdown, no explanation, no backticks.
The JSON must exactly match this schema:
{
  "machineType": string,
  "totalEstimatedTime": number,
  "generalNotes": string[],
  "operations": [
    {
      "opNumber": number,
      "name": string,
      "type": string,
      "description": string,
      "tool": {
        "type": string,
        "diameter": number,
        "material": string,
        "coated": boolean,
        "numberOfFlutes": number,
        "notes": string
      },
      "cuttingParams": {
        "spindleSpeed": number,
        "feedRate": number,
        "depthOfCut": number,
        "stepOver": number,
        "coolant": "flood"|"mist"|"air"|"none"
      },
      "setup": {
        "clampingMethod": string,
        "datumFace": string,
        "notes": string
      },
      "estimatedTime": number,
      "qualityChecks": string[],
      "gcodeSectionHint": string
    }
  ]
}`;
}

function buildUserPrompt(features: PartFeatures): string {
  const featureList = features.features
    .map((f) => `  - ${f.count}x ${f.type}${f.diameter ? ` Ø${f.diameter}mm` : ""}${f.depth ? ` depth=${f.depth}mm` : ""}${f.notes ? ` (${f.notes})` : ""}`)
    .join("\n");

  const toleranceList = features.tolerances
    .map((t) => `  - ${t.feature}: ${t.tolerance}`)
    .join("\n");

  return `Generate a complete CNC machining process plan for the following part:

PART: ${features.partName} | P/N: ${features.partNumber} | REV: ${features.revision}
MATERIAL: ${features.material}
TYPE: ${features.partType}
COMPLEXITY: ${features.estimatedComplexity}

DIMENSIONS (RAW):  ${features.rawDimensions.length} x ${features.rawDimensions.width} x ${features.rawDimensions.height} mm${features.rawDimensions.diameter ? ` | Ø${features.rawDimensions.diameter}mm` : ""}
STOCK DIMENSIONS:  ${features.stockDimensions.length} x ${features.stockDimensions.width} x ${features.stockDimensions.height} mm

FEATURES:
${featureList || "  - No specific features detected"}

TOLERANCES:
${toleranceList || "  - Standard ISO tolerances"}

SURFACE FINISHES:
${features.surfaceFinishes.map((sf) => `  - ${sf.feature}: Ra ${sf.ra}µm`).join("\n") || "  - Ra 3.2µm general"}

GUIDELINES:
- Use standard Fanuc G-code convention
- Each OP number increments by 10 (OP10, OP20, etc.)
- Choose cutting parameters appropriate for the material
- Include quality checks at each operation
- Minimize setups for cost efficiency
- For holes: use drilling cycle G81, for tapping use G84
- Specify tool numbers sequentially (T01, T02, ...)

Respond with ONLY the JSON object, no other text.`;
}

// ─── Provider Interface ───────────────────────────────────────────────────────

export interface CAPPProvider {
  generateProcessPlan(features: PartFeatures): Promise<ProcessPlan>;
  generateGCode(plan: ProcessPlan, features: PartFeatures, targetMachine: string): Promise<string>;
}

// ─── Cloud Provider (Anthropic) ───────────────────────────────────────────────

class AnthropicCAPPProvider implements CAPPProvider {
  private apiKey: string;
  private model: string;

  constructor() {
    this.apiKey = process.env.ANTHROPIC_API_KEY!;
    this.model  = process.env.ANTHROPIC_MODEL ?? "claude-3-5-sonnet-20241022";
  }

  async generateProcessPlan(features: PartFeatures): Promise<ProcessPlan> {
    const { default: Anthropic } = await import("@anthropic-ai/sdk");
    const client = new Anthropic({ apiKey: this.apiKey });

    const response = await client.messages.create({
      model: this.model,
      max_tokens: 8192,
      system: buildSystemPrompt(),
      messages: [{ role: "user", content: buildUserPrompt(features) }],
    });

    const text = response.content[0].type === "text" ? response.content[0].text : "{}";
    const parsed = parseJSONSafely(text);

    return assemblePlan(parsed, features, this.model);
  }

  async generateGCode(plan: ProcessPlan, features: PartFeatures, targetMachine: string): Promise<string> {
    const { default: Anthropic } = await import("@anthropic-ai/sdk");
    const client = new Anthropic({ apiKey: this.apiKey });

    const response = await client.messages.create({
      model: this.model,
      max_tokens: 8192,
      system: GCODE_SYSTEM_PROMPT,
      messages: [{ role: "user", content: buildGCodePrompt(plan, features, targetMachine) }],
    });

    const text = response.content[0].type === "text" ? response.content[0].text : "";
    return cleanGCode(text);
  }
}

// ─── Cloud Provider (Gemini) ──────────────────────────────────────────────────

class GeminiCAPPProvider implements CAPPProvider {
  private apiKey: string;
  private model: string;

  constructor() {
    this.apiKey = process.env.GEMINI_API_KEY!;
    this.model  = process.env.GEMINI_MODEL ?? "gemini-1.5-pro";
  }

  async generateProcessPlan(features: PartFeatures): Promise<ProcessPlan> {
    const { GoogleGenerativeAI } = await import("@google/generative-ai");
    const genAI  = new GoogleGenerativeAI(this.apiKey);
    const gemini = genAI.getGenerativeModel({
      model: this.model,
      systemInstruction: buildSystemPrompt(),
      generationConfig: { responseMimeType: "application/json" },
    });

    const result = await gemini.generateContent(buildUserPrompt(features));
    const text   = result.response.text();
    const parsed = parseJSONSafely(text);

    return assemblePlan(parsed, features, this.model);
  }

  async generateGCode(plan: ProcessPlan, features: PartFeatures, targetMachine: string): Promise<string> {
    const { GoogleGenerativeAI } = await import("@google/generative-ai");
    const genAI  = new GoogleGenerativeAI(this.apiKey);
    const gemini = genAI.getGenerativeModel({ model: this.model, systemInstruction: GCODE_SYSTEM_PROMPT });

    const result = await gemini.generateContent(buildGCodePrompt(plan, features, targetMachine));
    return cleanGCode(result.response.text());
  }
}

// ─── On-Prem Provider (Ollama / vLLM) ────────────────────────────────────────

class LocalCAPPProvider implements CAPPProvider {
  private baseUrl: string;
  private model: string;
  private gcodeModel: string;

  constructor() {
    this.baseUrl    = process.env.LOCAL_LLM_URL  ?? "http://localhost:11434/v1";
    this.model      = process.env.LOCAL_CAPP_MODEL  ?? process.env.LOCAL_LLM_MODEL ?? "qwen2.5:72b";
    this.gcodeModel = process.env.LOCAL_GCODE_MODEL ?? process.env.LOCAL_LLM_MODEL ?? "qwen2.5-coder:32b";
  }

  async generateProcessPlan(features: PartFeatures): Promise<ProcessPlan> {
    const { default: OpenAI } = await import("openai");
    const client = new OpenAI({ baseURL: this.baseUrl, apiKey: "ollama" });

    // For local models, prepend the JSON instruction more forcefully
    const systemWithFormat = buildSystemPrompt() +
      "\n\nCRITICAL: Return ONLY a raw JSON object. Start your response with { and end with }. No other text.";

    const response = await client.chat.completions.create({
      model: this.model,
      messages: [
        { role: "system", content: systemWithFormat },
        { role: "user",   content: buildUserPrompt(features) },
      ],
      response_format: { type: "json_object" },
    });

    const text   = response.choices[0]?.message?.content ?? "{}";
    const parsed = parseJSONSafely(text);

    return assemblePlan(parsed, features, this.model);
  }

  async generateGCode(plan: ProcessPlan, features: PartFeatures, targetMachine: string): Promise<string> {
    const { default: OpenAI } = await import("openai");
    const client = new OpenAI({ baseURL: this.baseUrl, apiKey: "ollama" });

    const response = await client.chat.completions.create({
      model: this.gcodeModel,
      messages: [
        { role: "system", content: GCODE_SYSTEM_PROMPT },
        { role: "user",   content: buildGCodePrompt(plan, features, targetMachine) },
      ],
    });

    return cleanGCode(response.choices[0]?.message?.content ?? "");
  }
}

// ─── G-Code Generation Helpers ────────────────────────────────────────────────

const GCODE_SYSTEM_PROMPT = `You are an expert CNC programmer specializing in Fanuc and Haas G-code.
Generate machine-ready G-code programs from process plans.
Output ONLY the G-code program. No explanation, no markdown, no backticks.
Start with % and end with %.
Use proper block numbering (N010, N020...).
Include tool changes, coolant, spindle control.
Use G54 work coordinate system.
Use G43 H## for tool length compensation.`;

function buildGCodePrompt(plan: ProcessPlan, features: PartFeatures, targetMachine: string): string {
  const opsText = plan.operations.map((op) => `
OP${op.opNumber} - ${op.name}
  Tool: T${String(op.opNumber / 10).padStart(2, "0")} ${op.tool.type} Ø${op.tool.diameter}mm
  Spindle: S${op.cuttingParams.spindleSpeed} RPM
  Feed: F${op.cuttingParams.feedRate} mm/min
  Depth of Cut: ${op.cuttingParams.depthOfCut}mm
  Coolant: ${op.cuttingParams.coolant}
  Operation: ${op.description}
  Hint: ${op.gcodeSectionHint ?? ""}
`).join("\n");

  return `Generate a complete ${targetMachine} G-code program for this process plan:

PART: ${plan.partName} | P/N: ${plan.partNumber} | REV: ${plan.revision}
MATERIAL: ${plan.material}
MACHINE: ${targetMachine}
STOCK: ${features.stockDimensions.length}x${features.stockDimensions.width}x${features.stockDimensions.height}mm

OPERATIONS:
${opsText}

Requirements:
- Fanuc-compatible G-code
- Program number O0001
- Safety line: G00 G17 G21 G40 G49 G80 G90
- Use G54 WCS
- Tool length compensation G43 H##
- Proper M03/M05 for spindle, M08/M09 for coolant
- End with M05, M09, G28 Z0, G28 X0 Y0, M30
- Add operation comments in parentheses

Output ONLY the G-code. Start with % on first line.`;
}

function cleanGCode(raw: string): string {
  // Remove markdown fences if present
  let code = raw.replace(/```[a-z]*/gi, "").replace(/```/g, "").trim();

  // Ensure starts with %
  if (!code.startsWith("%")) code = "%" + "\n" + code;
  // Ensure ends with %
  if (!code.endsWith("%")) code = code + "\n%";

  return code;
}

// ─── Shared Utilities ─────────────────────────────────────────────────────────

function parseJSONSafely(text: string): Record<string, unknown> {
  // Extract JSON from potential surrounding text
  const jsonMatch = text.match(/\{[\s\S]*\}/);
  if (!jsonMatch) return {};
  try {
    return JSON.parse(jsonMatch[0]) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function assemblePlan(
  parsed: Record<string, unknown>,
  features: PartFeatures,
  modelId: string
): ProcessPlan {
  const ops = Array.isArray(parsed.operations) ? parsed.operations as ProcessOperation[] : [];

  return {
    partName:             features.partName,
    partNumber:           features.partNumber,
    revision:             features.revision,
    material:             features.material,
    machineType:          (parsed.machineType as string) ?? "3-Axis VMC",
    totalEstimatedTime:   (parsed.totalEstimatedTime as number) ?? ops.reduce((s, o) => s + (o.estimatedTime ?? 0), 0),
    operations:           ops,
    generalNotes:         Array.isArray(parsed.generalNotes) ? parsed.generalNotes as string[] : [],
    generatedAt:          new Date().toISOString(),
    model:                modelId,
  };
}

// ─── Factory ──────────────────────────────────────────────────────────────────

export function createCAPPProvider(): CAPPProvider {
  const mode = process.env.DEPLOYMENT_MODE ?? "CLOUD";

  if (mode === "ON_PREM") return new LocalCAPPProvider();

  const cloudProvider = process.env.CLOUD_LLM_PROVIDER ?? "anthropic";
  return cloudProvider === "gemini" ? new GeminiCAPPProvider() : new AnthropicCAPPProvider();
}
