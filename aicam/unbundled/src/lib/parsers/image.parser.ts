import type { LLMProvider } from "../types";

export interface ParsedImage {
  description: string;
  ocrText: string;
  width: number;
  height: number;
}

/**
 * Extract semantic description and OCR text from an image buffer
 * using a Vision LLM (Anthropic Claude or Gemini).
 * Falls back to metadata-only if vision is unavailable.
 */
export async function parseImage(
  buffer: Buffer,
  mimeType: string,
  llm: LLMProvider,
  filename: string
): Promise<ParsedImage> {
  // Get image dimensions with sharp
  const sharp = (await import("sharp")).default;
  const meta = await sharp(buffer).metadata();
  const width = meta.width ?? 0;
  const height = meta.height ?? 0;

  try {
    // Use Claude vision for description + OCR
    const base64 = buffer.toString("base64");
    const description = await describeImageWithVision(base64, mimeType, filename);
    return { description, ocrText: description, width, height };
  } catch (err) {
    console.warn("Vision LLM unavailable, using fallback image parser:", err);
    return {
      description: `Image file: ${filename} (${width}x${height}px)`,
      ocrText: "",
      width,
      height,
    };
  }
}

async function describeImageWithVision(
  base64: string,
  mimeType: string,
  filename: string
): Promise<string> {
  const deploymentMode = process.env.DEPLOYMENT_MODE ?? "CLOUD";

  if (deploymentMode === "ON_PREM") {
    // On-prem: use Ollama vision model (e.g., llava)
    const response = await fetch(`${process.env.LOCAL_LLM_URL ?? "http://localhost:11434/v1"}/chat/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: "llava",
        messages: [
          {
            role: "user",
            content: [
              {
                type: "image_url",
                image_url: { url: `data:${mimeType};base64,${base64}` },
              },
              {
                type: "text",
                text: `You are an expert manufacturing engineer. Describe this engineering document "${filename}" in detail.
Extract all visible text, part numbers, tolerances, material specs, revision numbers, and drawing notes.
Format your response as: DESCRIPTION: <description> | OCR_TEXT: <all extracted text>`,
              },
            ],
          },
        ],
      }),
    });
    const data = (await response.json()) as { choices: { message: { content: string } }[] };
    return data.choices[0]?.message?.content ?? "";
  }

  // Cloud: use Anthropic Claude vision
  const { default: Anthropic } = await import("@anthropic-ai/sdk");
  const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY! });

  const response = await client.messages.create({
    model: process.env.ANTHROPIC_MODEL ?? "claude-3-5-sonnet-20241022",
    max_tokens: 1024,
    messages: [
      {
        role: "user",
        content: [
          {
            type: "image",
            source: {
              type: "base64",
              media_type: mimeType as "image/jpeg" | "image/png" | "image/gif" | "image/webp",
              data: base64,
            },
          },
          {
            type: "text",
            text: `You are an expert manufacturing engineer. Analyze this engineering document "${filename}".
Extract all visible text, part numbers, tolerances, material specs, revision numbers, and technical notes.
Format: DESCRIPTION: <brief description> | OCR_TEXT: <all extracted text verbatim>`,
          },
        ],
      },
    ],
  });

  const block = response.content[0];
  return block.type === "text" ? block.text : "";
}
