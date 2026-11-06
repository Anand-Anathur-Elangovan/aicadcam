import type { RerankProvider } from "../types";

// ─── Voyage AI Reranker ───────────────────────────────────────────────────────
class VoyageRerankProvider implements RerankProvider {
  private apiKey: string;
  private model: string;

  constructor() {
    this.apiKey = process.env.VOYAGE_API_KEY!;
    this.model = process.env.VOYAGE_RERANK_MODEL ?? "rerank-2";
  }

  async rerank(query: string, documents: string[]): Promise<{ index: number; score: number }[]> {
    const response = await fetch("https://api.voyageai.com/v1/rerank", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${this.apiKey}`,
      },
      body: JSON.stringify({ query, documents, model: this.model }),
    });

    if (!response.ok) throw new Error(`Voyage rerank failed: ${await response.text()}`);

    const data = (await response.json()) as { data: { index: number; relevance_score: number }[] };
    return data.data.map((d) => ({ index: d.index, score: d.relevance_score }));
  }
}

// ─── Local BGE-Reranker (via HTTP sidecar) ────────────────────────────────────
// Expects a local FastAPI/Flask server at LOCAL_RERANKER_URL
// with POST /rerank -> { results: [{index, score}] }
class LocalRerankProvider implements RerankProvider {
  private baseUrl: string;

  constructor() {
    this.baseUrl = process.env.LOCAL_RERANKER_URL ?? "http://localhost:8001/rerank";
  }

  async rerank(query: string, documents: string[]): Promise<{ index: number; score: number }[]> {
    const response = await fetch(this.baseUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, documents }),
    });

    if (!response.ok) {
      // Graceful fallback: return identity ordering if reranker is unavailable
      console.warn("Local reranker unavailable, using identity order");
      return documents.map((_, i) => ({ index: i, score: 1 - i * 0.01 }));
    }

    const data = (await response.json()) as { results: { index: number; score: number }[] };
    return data.results;
  }
}

// ─── Factory ─────────────────────────────────────────────────────────────────
export function createRerankProvider(): RerankProvider {
  const mode = process.env.DEPLOYMENT_MODE ?? "CLOUD";
  return mode === "ON_PREM" ? new LocalRerankProvider() : new VoyageRerankProvider();
}
