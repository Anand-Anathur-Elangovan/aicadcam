import type { LLMProvider } from "../types";

// ─── Anthropic Claude Provider ───────────────────────────────────────────────
class AnthropicLLMProvider implements LLMProvider {
  private apiKey: string;
  private model: string;

  constructor() {
    this.apiKey = process.env.ANTHROPIC_API_KEY!;
    this.model = process.env.ANTHROPIC_MODEL ?? "claude-3-5-sonnet-20241022";
  }

  async *streamChat({ messages, systemPrompt }: Parameters<LLMProvider["streamChat"]>[0]): AsyncIterable<string> {
    const { default: Anthropic } = await import("@anthropic-ai/sdk");
    const client = new Anthropic({ apiKey: this.apiKey });

    const stream = client.messages.stream({
      model: this.model,
      max_tokens: 4096,
      system: systemPrompt,
      messages: messages as Parameters<typeof client.messages.create>[0]["messages"],
    });

    for await (const event of stream) {
      if (event.type === "content_block_delta" && event.delta.type === "text_delta") {
        yield event.delta.text;
      }
    }
  }

  async chat({ messages, systemPrompt }: Parameters<LLMProvider["chat"]>[0]): Promise<string> {
    const { default: Anthropic } = await import("@anthropic-ai/sdk");
    const client = new Anthropic({ apiKey: this.apiKey });

    const response = await client.messages.create({
      model: this.model,
      max_tokens: 4096,
      system: systemPrompt,
      messages: messages as Parameters<typeof client.messages.create>[0]["messages"],
    });

    const block = response.content[0];
    return block.type === "text" ? block.text : "";
  }
}

// ─── Gemini Provider ─────────────────────────────────────────────────────────
class GeminiLLMProvider implements LLMProvider {
  private apiKey: string;
  private model: string;

  constructor() {
    this.apiKey = process.env.GEMINI_API_KEY!;
    this.model = process.env.GEMINI_MODEL ?? "gemini-1.5-pro";
  }

  async *streamChat({ messages, systemPrompt }: Parameters<LLMProvider["streamChat"]>[0]): AsyncIterable<string> {
    const { GoogleGenerativeAI } = await import("@google/generative-ai");
    const genAI = new GoogleGenerativeAI(this.apiKey);
    const gemini = genAI.getGenerativeModel({ model: this.model, systemInstruction: systemPrompt });

    const history = messages.slice(0, -1).map((m) => ({
      role: m.role === "assistant" ? "model" : "user",
      parts: [{ text: m.content }],
    }));
    const lastMessage = messages[messages.length - 1];

    const chat = gemini.startChat({ history });
    const result = await chat.sendMessageStream(lastMessage.content);

    for await (const chunk of result.stream) {
      yield chunk.text();
    }
  }

  async chat({ messages, systemPrompt }: Parameters<LLMProvider["chat"]>[0]): Promise<string> {
    const { GoogleGenerativeAI } = await import("@google/generative-ai");
    const genAI = new GoogleGenerativeAI(this.apiKey);
    const gemini = genAI.getGenerativeModel({ model: this.model, systemInstruction: systemPrompt });

    const history = messages.slice(0, -1).map((m) => ({
      role: m.role === "assistant" ? "model" : "user",
      parts: [{ text: m.content }],
    }));
    const lastMessage = messages[messages.length - 1];

    const chat = gemini.startChat({ history });
    const result = await chat.sendMessage(lastMessage.content);
    return result.response.text();
  }
}

// ─── Local Ollama / vLLM Provider (OpenAI-compatible) ───────────────────────
class LocalLLMProvider implements LLMProvider {
  private baseUrl: string;
  private model: string;

  constructor() {
    this.baseUrl = process.env.LOCAL_LLM_URL ?? "http://localhost:11434/v1";
    this.model = process.env.LOCAL_LLM_MODEL ?? "qwen2.5:72b";
  }

  async *streamChat({ messages, systemPrompt }: Parameters<LLMProvider["streamChat"]>[0]): AsyncIterable<string> {
    const { default: OpenAI } = await import("openai");
    const client = new OpenAI({ baseURL: this.baseUrl, apiKey: "ollama" });

    const stream = await client.chat.completions.create({
      model: this.model,
      stream: true,
      messages: [{ role: "system", content: systemPrompt }, ...messages] as Parameters<
        typeof client.chat.completions.create
      >[0]["messages"],
    });

    for await (const chunk of stream) {
      const delta = chunk.choices[0]?.delta?.content;
      if (delta) yield delta;
    }
  }

  async chat({ messages, systemPrompt }: Parameters<LLMProvider["chat"]>[0]): Promise<string> {
    const { default: OpenAI } = await import("openai");
    const client = new OpenAI({ baseURL: this.baseUrl, apiKey: "ollama" });

    const response = await client.chat.completions.create({
      model: this.model,
      messages: [{ role: "system", content: systemPrompt }, ...messages] as Parameters<
        typeof client.chat.completions.create
      >[0]["messages"],
    });

    return response.choices[0]?.message?.content ?? "";
  }
}

// ─── Factory ─────────────────────────────────────────────────────────────────
export function createLLMProvider(): LLMProvider {
  const mode = process.env.DEPLOYMENT_MODE ?? "CLOUD";

  if (mode === "ON_PREM") {
    return new LocalLLMProvider();
  }

  const cloudProvider = process.env.CLOUD_LLM_PROVIDER ?? "anthropic";
  return cloudProvider === "gemini" ? new GeminiLLMProvider() : new AnthropicLLMProvider();
}
