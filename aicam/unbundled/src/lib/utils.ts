import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatBytes(bytes: number, decimals = 2) {
  if (bytes === 0) return "0 Bytes";
  const k = 1024;
  const sizes = ["Bytes", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(decimals))} ${sizes[i]}`;
}

export function slugify(text: string) {
  return text.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "");
}

export const DOCUMENT_CATEGORIES = [
  "DRAWING_2D",
  "INSPECTION_FIR",
  "FINANCIAL_PO_INVOICE",
  "STORES_FG",
  "TOOLING_MATERIAL",
  "GENERAL",
] as const;

export type DocumentCategory = (typeof DOCUMENT_CATEGORIES)[number];

export const CATEGORY_LABELS: Record<DocumentCategory, string> = {
  DRAWING_2D: "2D Drawing",
  INSPECTION_FIR: "Inspection / FIR",
  FINANCIAL_PO_INVOICE: "PO / Invoice",
  STORES_FG: "Stores / FG",
  TOOLING_MATERIAL: "Tooling / Material",
  GENERAL: "General",
};

export const CATEGORY_COLORS: Record<DocumentCategory, string> = {
  DRAWING_2D: "bg-blue-100 text-blue-800",
  INSPECTION_FIR: "bg-purple-100 text-purple-800",
  FINANCIAL_PO_INVOICE: "bg-green-100 text-green-800",
  STORES_FG: "bg-orange-100 text-orange-800",
  TOOLING_MATERIAL: "bg-red-100 text-red-800",
  GENERAL: "bg-gray-100 text-gray-800",
};
