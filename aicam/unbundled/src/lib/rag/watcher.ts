import path from "path";
import type { DocumentCategory } from "../utils";
import { ingestFile } from "./ingest";

const SUPPORTED_EXTENSIONS: Record<string, string> = {
  ".pdf": "application/pdf",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".xls": "application/vnd.ms-excel",
  ".csv": "text/csv",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
};

/**
 * Detect document category from filename heuristics.
 */
function detectCategory(filename: string): DocumentCategory {
  const lower = filename.toLowerCase();
  if (lower.includes("dwg") || lower.includes("drawing") || lower.includes("2d")) return "DRAWING_2D";
  if (lower.includes("fir") || lower.includes("inspection") || lower.includes("quality")) return "INSPECTION_FIR";
  if (lower.includes("po") || lower.includes("invoice") || lower.includes("purchase")) return "FINANCIAL_PO_INVOICE";
  if (lower.includes("store") || lower.includes("inventory") || lower.includes("fg") || lower.includes("stock")) return "STORES_FG";
  if (lower.includes("tool") || lower.includes("material") || lower.includes("mtc") || lower.includes("catalog")) return "TOOLING_MATERIAL";
  return "GENERAL";
}

/**
 * Start a chokidar-based folder watcher for on-prem local sync.
 * Only active when DEPLOYMENT_MODE=ON_PREM and LOCAL_WATCH_DIRECTORY is set.
 */
export async function startFolderWatcher(watchDir?: string): Promise<() => void> {
  const dir = watchDir ?? process.env.LOCAL_WATCH_DIRECTORY;

  if (!dir) {
    console.log("No LOCAL_WATCH_DIRECTORY set, folder watcher disabled.");
    return () => {};
  }

  const chokidar = await import("chokidar");
  const fs = await import("fs/promises");

  const watcher = chokidar.watch(dir, {
    ignored: /(^|[/\\])\../,
    persistent: true,
    ignoreInitial: false,
    awaitWriteFinish: { stabilityThreshold: 2000, pollInterval: 100 },
  });

  const processFile = async (filePath: string) => {
    const ext = path.extname(filePath).toLowerCase();
    const mimeType = SUPPORTED_EXTENSIONS[ext];
    if (!mimeType) return;

    try {
      const buffer = await fs.readFile(filePath);
      const originalName = path.basename(filePath);
      const filename = `${Date.now()}_${originalName}`;
      const category = detectCategory(originalName);

      console.log(`[Watcher] Ingesting: ${originalName} → ${category}`);
      await ingestFile({ buffer, filename, originalName, mimeType, category, sourcePath: filePath });
      console.log(`[Watcher] Indexed: ${originalName}`);
    } catch (err) {
      console.error(`[Watcher] Failed to ingest ${filePath}:`, err);
    }
  };

  watcher
    .on("add", processFile)
    .on("change", processFile)
    .on("error", (err) => console.error("[Watcher] Error:", err));

  console.log(`[Watcher] Watching: ${dir}`);
  return () => watcher.close();
}
