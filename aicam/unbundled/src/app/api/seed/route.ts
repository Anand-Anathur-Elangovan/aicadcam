import { createVectorProvider } from "@/lib/providers/vector.provider";
import type { DocumentCategory } from "@/lib/utils";

export const runtime = "nodejs";

type SeedDoc = {
  filename: string;
  originalName: string;
  category: DocumentCategory;
  mimeType: string;
  fileSize: number;
  storagePath: string;
  storageBucket: string;
  pageCount?: number;
  status: "indexed";
  docTitle?: string;
  description?: string;
  partName?: string;
  partNumber?: string;
  revision?: string;
  developedDate?: string;
  customerName?: string;
  issueDetails?: string;
  metadata: Record<string, unknown>;
  autoExtracted?: Record<string, string>;
};

const SAMPLE_DOCS: SeedDoc[] = [
  {
    filename: "bracket-assembly-rev-c.pdf",
    originalName: "Bracket Assembly Drawing Rev C.pdf",
    category: "DRAWING_2D",
    mimeType: "application/pdf",
    fileSize: 1_245_600,
    storagePath: "DRAWING_2D/bracket-assembly-rev-c.pdf",
    storageBucket: "manufacturing-docs",
    pageCount: 6,
    status: "indexed",
    docTitle: "Bracket Assembly Engineering Drawing",
    description: "Front-face bracket for Panel A mounting system. Tolerances per ISO 2768-m. Surface finish Ra 3.2 µm all over unless noted.",
    partName: "Main Bracket",
    partNumber: "BRK-ASY-001",
    revision: "C",
    developedDate: "2024-01-15",
    customerName: "ACME Aerospace Corp",
    metadata: { material: "Aluminum 6061-T6", finish: "Anodize Type III Class 2", weight: "0.42 kg" },
    autoExtracted: { partNumber: "BRK-ASY-001", revision: "C" },
  },
  {
    filename: "flange-body-dwg-a901-rev3.pdf",
    originalName: "Flange Body Drawing DWG-A901 Rev3.pdf",
    category: "DRAWING_2D",
    mimeType: "application/pdf",
    fileSize: 892_400,
    storagePath: "DRAWING_2D/flange-body-dwg-a901-rev3.pdf",
    storageBucket: "manufacturing-docs",
    pageCount: 4,
    status: "indexed",
    docTitle: "Flange Body Engineering Drawing",
    description: "Hydraulic flange body with 4x M12-1.75 bolt pattern on 80 mm PCD. Bore diameter Ø28H7. Surface finish Ra 1.6 µm on sealing faces.",
    partName: "Flange Body",
    partNumber: "FLG-BODY-A901",
    revision: "3",
    developedDate: "2024-02-20",
    customerName: "Hydratech Industries",
    metadata: { material: "Stainless Steel 316L", surface: "Ra 1.6 µm sealing faces", pressure: "350 bar rated" },
    autoExtracted: { partNumber: "FLG-BODY-A901", revision: "3" },
  },
  {
    filename: "fir-brk-asy-001-rev-c-lot-0301.pdf",
    originalName: "FIR - Bracket Assembly BRK-ASY-001 Rev C Lot 0301.pdf",
    category: "INSPECTION_FIR",
    mimeType: "application/pdf",
    fileSize: 645_800,
    storagePath: "INSPECTION_FIR/fir-brk-asy-001-rev-c-lot-0301.pdf",
    storageBucket: "manufacturing-docs",
    pageCount: 12,
    status: "indexed",
    docTitle: "First Article Inspection Report — Bracket Assembly",
    description: "AS9102 First Article Inspection Report for BRK-ASY-001 Rev C production lot QC-2024-0301. 25 pieces inspected using Renishaw CMM. All critical dimensions within tolerance. 0 non-conformances raised.",
    partName: "Main Bracket",
    partNumber: "BRK-ASY-001",
    revision: "C",
    developedDate: "2024-03-01",
    customerName: "ACME Aerospace Corp",
    issueDetails: "Lot QC-2024-0301, 25 pcs, 0 non-conformances, all CTQs pass",
    metadata: { inspector: "J. Martinez", approved: true, cmmSerial: "R-CMM-007", temperature: "20.1°C" },
    autoExtracted: { partNumber: "BRK-ASY-001", revision: "C" },
  },
  {
    filename: "fir-flg-body-a901-rev3.pdf",
    originalName: "FIR - Flange Body FLG-BODY-A901 Rev 3.pdf",
    category: "INSPECTION_FIR",
    mimeType: "application/pdf",
    fileSize: 512_000,
    storagePath: "INSPECTION_FIR/fir-flg-body-a901-rev3.pdf",
    storageBucket: "manufacturing-docs",
    pageCount: 8,
    status: "indexed",
    docTitle: "First Article Inspection Report — Flange Body",
    description: "Dimensional inspection with CMM measurement data for FLG-BODY-A901 Rev 3. Bore Ø28H7 measured at 28.018 mm (+0.018 / 0.000). All sealing face flatness values ≤ 0.003 mm.",
    partName: "Flange Body",
    partNumber: "FLG-BODY-A901",
    revision: "3",
    developedDate: "2024-03-15",
    customerName: "Hydratech Industries",
    issueDetails: "Pressure test 350 bar for 30 min — no leakage detected",
    metadata: { cmm: "Renishaw REVO-2", temperature: "20°C ±0.5°C", boreActual: "28.018 mm" },
    autoExtracted: { partNumber: "FLG-BODY-A901" },
  },
  {
    filename: "po-2024-0456-aluminum-6061.pdf",
    originalName: "Purchase Order PO-2024-0456 — Aluminum 6061-T6 Bar Stock.pdf",
    category: "FINANCIAL_PO_INVOICE",
    mimeType: "application/pdf",
    fileSize: 234_500,
    storagePath: "FINANCIAL_PO_INVOICE/po-2024-0456-aluminum-6061.pdf",
    storageBucket: "manufacturing-docs",
    pageCount: 3,
    status: "indexed",
    docTitle: "Purchase Order PO-2024-0456",
    description: "Purchase order for 500 kg Aluminum 6061-T6 bar stock (Ø50 mm × 1000 mm) from Metal Express Inc. Material cert and test reports required per ASTM B221.",
    partName: "Aluminum 6061-T6 Bar Stock",
    developedDate: "2024-03-20",
    customerName: "Metal Express Inc.",
    metadata: { poNumber: "PO-2024-0456", amount: "$18,750 USD", delivery: "2024-04-10", spec: "ASTM B221" },
    autoExtracted: {},
  },
  {
    filename: "tooling-spec-t01-coromill-390.pdf",
    originalName: "T01 Face Mill Tooling Specification — CoroMill 390.pdf",
    category: "TOOLING_MATERIAL",
    mimeType: "application/pdf",
    fileSize: 389_200,
    storagePath: "TOOLING_MATERIAL/tooling-spec-t01-coromill-390.pdf",
    storageBucket: "manufacturing-docs",
    pageCount: 5,
    status: "indexed",
    docTitle: "Face Mill T01 — Sandvik CoroMill 390 Specification",
    description: "Sandvik Coromant CoroMill 390 50 mm face mill specification, cutting data, and recommended parameters for Aluminum 6061-T6. vc = 800 m/min, fz = 0.15 mm/tooth.",
    partName: "CoroMill 390 50 mm Face Mill",
    partNumber: "390-050Q22-11M",
    metadata: { brand: "Sandvik Coromant", diameter: "50 mm", inserts: "R390-11 T3 08M-PM 1030", coolant: "emulsion 6%" },
    autoExtracted: { partNumber: "390-050Q22-11M" },
  },
  {
    filename: "stores-fg-q1-2024.xlsx",
    originalName: "Finished Goods Inventory Q1 2024.xlsx",
    category: "STORES_FG",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    fileSize: 156_800,
    storagePath: "STORES_FG/stores-fg-q1-2024.xlsx",
    storageBucket: "manufacturing-docs",
    status: "indexed",
    docTitle: "Finished Goods Inventory — Q1 2024",
    description: "Quarterly stock report: 350 units BRK-ASY-001 Rev C, 120 units FLG-BODY-A901 Rev 3, 75 units miscellaneous fasteners. Total valuation USD 234,500.",
    developedDate: "2024-03-31",
    metadata: { quarter: "Q1 2024", totalItems: 545, valuationUSD: "$234,500", warehouseLocation: "Bay 3-A" },
    autoExtracted: {},
  },
  {
    filename: "as9100-qms-manual-rev9.pdf",
    originalName: "AS9100 Quality Management System Manual Rev 9.pdf",
    category: "GENERAL",
    mimeType: "application/pdf",
    fileSize: 2_890_400,
    storagePath: "GENERAL/as9100-qms-manual-rev9.pdf",
    storageBucket: "manufacturing-docs",
    pageCount: 87,
    status: "indexed",
    docTitle: "AS9100 QMS Manual",
    description: "Company Quality Management System manual compliant with AS9100 Rev D. Includes process descriptions, ITAR handling procedures, control plan, and design authority sections.",
    revision: "9",
    developedDate: "2023-12-01",
    metadata: { standard: "AS9100 Rev D", itar: true, classification: "UNCONTROLLED WHEN PRINTED", nextReviewDate: "2025-12-01" },
    autoExtracted: { revision: "9" },
  },
];

// POST /api/seed  — Insert sample manufacturing documents
export async function POST() {
  try {
    const vector = createVectorProvider();
    const results: { id: string; name: string }[] = [];

    for (const doc of SAMPLE_DOCS) {
      const id = await vector.upsertDocument(doc);
      results.push({ id, name: doc.originalName });
    }

    return Response.json({
      success: true,
      message: `Seeded ${results.length} sample manufacturing documents`,
      documents: results,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Seed failed";
    console.error("[/api/seed] Error:", err);
    return Response.json(
      {
        success: false,
        error: message,
        hint: "Ensure your database is running and DEPLOYMENT_MODE / connection env vars are set.",
      },
      { status: 500 }
    );
  }
}

// GET /api/seed  — Preview seed data without inserting
export async function GET() {
  return Response.json({
    count: SAMPLE_DOCS.length,
    documents: SAMPLE_DOCS.map((d) => ({
      filename: d.originalName,
      category: d.category,
      partNumber: d.partNumber ?? null,
      revision: d.revision ?? null,
      fileSize: d.fileSize,
    })),
    usage: "POST /api/seed to insert these documents into the database",
  });
}
