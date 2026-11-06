import type { EmbeddingProvider } from "../types";

// ─── Voyage AI Provider ───────────────────────────────────────────────────────
class VoyageEmbeddingProvider implements EmbeddingProvider {
  readonly dimensions = 1024;
  private apiKey: string;
  private model: string;

  constructor() {
    this.apiKey = process.env.VOYAGE_API_KEY!;
    this.model = process.env.VOYAGE_EMBEDDING_MODEL ?? "voyage-multimodal-3";
  }

  async embed(text: string): Promise<number[]> {
    const results = await this.embedBatch([text]);
    return results[0];
  }

  async embedBatch(texts: string[]): Promise<number[][]> {
    const response = await fetch("https://api.voyageai.com/v1/embeddings", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.apiKey}`,
      },
      body: JSON.stringify({ input: texts, model: this.model }),
    });

    if (!response.ok) {
      const err = await response.text();
      throw new Error(`Voyage embedding failed: ${err}`);
    }

    const data = (await response.json()) as { data: { embedding: number[] }[] };
    return data.data.map((d) => d.embedding);
  }
}

// ─── Local Ollama Embedding Provider ─────────────────────────────────────────
class LocalOllamaEmbeddingProvider implements EmbeddingProvider {
  readonly dimensions = 1024;
  private baseUrl: string;
  private model: string;

  constructor() {
    this.baseUrl = process.env.LOCAL_EMBEDDING_URL ?? "http://localhost:11434/api/embeddings";
    this.model = process.env.LOCAL_EMBEDDING_MODEL ?? "bge-m3";
  }

  async embed(text: string): Promise<number[]> {
    const response = await fetch(this.baseUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: this.model, prompt: text }),
    });

    if (!response.ok) throw new Error(`Local embedding failed: ${await response.text()}`);
    const data = (await response.json()) as { embedding: number[] };
    return data.embedding;
  }

  async embedBatch(texts: string[]): Promise<number[][]> {
    // Ollama doesn't have native batch endpoint; parallelize with concurrency cap
    const CONCURRENCY = 5;
    const results: number[][] = [];

    for (let i = 0; i < texts.length; i += CONCURRENCY) {
      const batch = texts.slice(i, i + CONCURRENCY);
      const batchResults = await Promise.all(batch.map((t) => this.embed(t)));
      results.push(...batchResults);
    }

    return results;
  }
}

// ─── Factory ─────────────────────────────────────────────────────────────────
export function createEmbeddingProvider(): EmbeddingProvider {
  const mode = process.env.DEPLOYMENT_MODE ?? "CLOUD";
  return mode === "ON_PREM" ? new LocalOllamaEmbeddingProvider() : new VoyageEmbeddingProvider();
}
