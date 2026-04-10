import { NextRequest, NextResponse } from "next/server";
import { PrismaClient } from "@prisma/client";
import { PrismaBetterSqlite3 } from "@prisma/adapter-better-sqlite3";
import { storageClient } from "@/lib/storage-client";

const globalForPrisma = global as unknown as { prisma: PrismaClient };
let prisma = globalForPrisma.prisma;
if (!prisma) {
  const adapter = new PrismaBetterSqlite3({
    url: process.env.DATABASE_URL || "file:./dev.db"
  });
  prisma = new PrismaClient({ adapter });
  globalForPrisma.prisma = prisma;
}

const LATEX_SERVICE_URL = (() => {
  const raw = process.env.LATEX_SERVICE_URL || "http://localhost:8002";
  const sanitized = raw.replace(/['"]/g, "").trim();
  return sanitized.startsWith("http") ? sanitized : `http://${sanitized}`;
})();

const INTERNAL_API_KEY = process.env.INTERNAL_API_KEY || "default_secret_key_change_me";

/**
 * GET /api/resume/download?threadId=...&format=pdf|tex&filename=...
 *
 * - format=tex: Stream the stored .tex file from S3 directly.
 * - format=pdf (default):
 *     - If latexHash === pdfHash → serve cached PDF from S3.
 *     - Otherwise → call LaTeX Service /compile, get new PDF, update DB, serve.
 */
export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const threadId = searchParams.get("threadId");
  const format = searchParams.get("format") || "pdf";
  const filename = searchParams.get("filename") || "resume";

  if (!threadId) {
    return NextResponse.json({ error: "Missing threadId" }, { status: 400 });
  }

  // Load thread from DB
  const thread = await prisma.chatThread.findUnique({ where: { id: threadId } });
  if (!thread) {
    return NextResponse.json({ error: "Thread not found" }, { status: 404 });
  }

  // ── LaTeX Download ────────────────────────────────────────────────────────
  if (format === "tex") {
    if (!thread.texKey) {
      return NextResponse.json(
        { error: "No rendered LaTeX found. Ask the agent to prepare the resume first." },
        { status: 404 }
      );
    }

    let texContent: Response;
    try {
      texContent = await storageClient.download(thread.texKey);
    } catch (e) {
      console.error("[download/tex] Storage error:", e);
      return NextResponse.json({ error: "Failed to fetch .tex from storage" }, { status: 502 });
    }

    const texBytes = await texContent.arrayBuffer();
    const safeName = filename.endsWith(".tex") ? filename : `${filename}.tex`;

    return new NextResponse(texBytes as unknown as BodyInit, {
      headers: {
        "Content-Type": "application/x-tex",
        "Content-Disposition": `attachment; filename="${safeName}"`,
      },
    });
  }

  // ── PDF Download ──────────────────────────────────────────────────────────
  if (!thread.texKey) {
    return NextResponse.json(
      { error: "No rendered LaTeX found. Ask the agent to prepare the resume first." },
      { status: 404 }
    );
  }

  const isCached =
    thread.latexHash &&
    thread.pdfHash &&
    thread.latexHash === thread.pdfHash &&
    thread.pdfKey;

  let pdfBytes: ArrayBuffer;

  if (isCached) {
    // ── Cache hit: serve existing PDF from storage ────────────────────────
    console.log(`[download/pdf] Cache hit for thread ${threadId}, serving ${thread.pdfKey}`);
    try {
      const pdfResp = await storageClient.download(thread.pdfKey!);
      pdfBytes = await pdfResp.arrayBuffer();
    } catch (e) {
      console.error("[download/pdf] Storage error fetching cached PDF:", e);
      return NextResponse.json({ error: "Failed to fetch cached PDF from storage" }, { status: 502 });
    }
  } else {
    // ── Cache miss: download .tex, compile, upload PDF ────────────────────
    console.log(`[download/pdf] Cache miss for thread ${threadId}, recompiling...`);

    // 1. Fetch .tex from storage
    let texContent: string;
    try {
      const texResp = await storageClient.download(thread.texKey);
      texContent = await texResp.text();
    } catch (e) {
      console.error("[download/pdf] Failed to fetch .tex:", e);
      return NextResponse.json({ error: "Failed to fetch .tex from storage" }, { status: 502 });
    }

    // 2. Call LaTeX Service /compile
    let pdfKey: string;
    try {
      const compileResp = await fetch(`${LATEX_SERVICE_URL}/compile`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": INTERNAL_API_KEY,
        },
        body: JSON.stringify({ tex_content: texContent, thread_id: threadId }),
        // Allow up to 3 minutes for compilation
        signal: AbortSignal.timeout(180_000),
      });

      if (!compileResp.ok) {
        const errText = await compileResp.text();
        console.error("[download/pdf] Compile error:", errText);
        return NextResponse.json(
          { error: `LaTeX compilation failed: ${errText}` },
          { status: 502 }
        );
      }

      const result = await compileResp.json();
      pdfKey = result.pdf_key;
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      console.error("[download/pdf] LaTeX Service error:", msg);
      return NextResponse.json(
        { error: `Could not reach compilation service: ${msg}` },
        { status: 502 }
      );
    }

    // 3. Update DB: pdfKey + pdfHash = latexHash
    await prisma.chatThread.update({
      where: { id: threadId },
      data: { pdfKey, pdfHash: thread.latexHash },
    });

    // 4. Fetch the compiled PDF from storage
    try {
      const pdfResp = await storageClient.download(pdfKey);
      pdfBytes = await pdfResp.arrayBuffer();
    } catch (e) {
      console.error("[download/pdf] Failed to fetch compiled PDF:", e);
      return NextResponse.json({ error: "Failed to fetch compiled PDF from storage" }, { status: 502 });
    }
  }

  const safeName = filename.endsWith(".pdf") ? filename : `${filename}.pdf`;
  return new NextResponse(pdfBytes as unknown as BodyInit, {
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": `attachment; filename="${safeName}"`,
    },
  });
}
