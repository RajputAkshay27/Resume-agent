import { NextRequest, NextResponse } from "next/server";
import prisma from "@/lib/db";
import { storageClient } from "@/lib/storage-client";

const BUCKET_NAME = process.env.S3_BUCKET || "resume_agent_bucket";

export async function GET(req: NextRequest) {
  try {
    const authHeader = req.headers.get("authorization");
    if (authHeader !== `Bearer ${process.env.NEXTAUTH_SECRET}`) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { searchParams } = new URL(req.url);
    const threadId = searchParams.get("threadId");
    const pdfKey = searchParams.get("pdfKey");

    // Case 1: Proxy streaming a binary PDF
    if (pdfKey) {
      try {
        const response = await storageClient.download(pdfKey);
        const bodyArray = await response.arrayBuffer();
        return new NextResponse(bodyArray as any, {
          headers: {
            "Content-Type": "application/pdf",
            "Content-Disposition": `attachment; filename="resume.pdf"`,
          },
        });
      } catch (error) {
        console.error(`Storage proxy error for ${pdfKey}:`, error);
        return NextResponse.json({ error: "Failed to fetch PDF" }, { status: 404 });
      }
    }

    if (!threadId) {
      return NextResponse.json({ error: "Missing threadId" }, { status: 400 });
    }

    const thread = await prisma.chatThread.findUnique({
      where: { id: threadId },
      include: { user: { select: { masterProfile: true, templateKey: true } } },
    });

    if (!thread) {
      return NextResponse.json({ error: "Thread not found" }, { status: 404 });
    }

    // Fetch the template content from Storage Service
    let templateContent: string | null = null;
    const activeTemplateKey = thread.templateKey || thread.user?.templateKey;
    if (activeTemplateKey) {
      try {
        const response = await storageClient.download(activeTemplateKey);
        templateContent = await response.text();
      } catch (e) {
        console.error("Failed to fetch template from Storage Service:", e);
      }
    }

    return NextResponse.json({
      jobDescription: thread.jobDescription || null,
      jobUrl: thread.jobUrl || null,
      tailoredProfile: thread.tailoredProfile || null,
      templateKey: activeTemplateKey || null,
      templateContent,
      sectionPrefs: thread.sectionPrefs || null,
      masterProfile: thread.user?.masterProfile || null,
      pdfKey: thread.pdfKey || null,
      pdfHash: thread.pdfHash || null,
      texKey: thread.texKey || null,
      latexHash: thread.latexHash || null,
      updatedAt: thread.updatedAt,
    });
  } catch (error) {
    console.error("Internal Context GET error:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const authHeader = req.headers.get("authorization");
    if (authHeader !== `Bearer ${process.env.NEXTAUTH_SECRET}`) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const {
      threadId,
      tailoredProfile,
      pdfKey,
      pdfHash,
      pdfData,
      deleteOldPdfKey,
      texKey,
      latexHash,
    } = await req.json();

    if (!threadId) {
      return NextResponse.json({ error: "Missing threadId" }, { status: 400 });
    }

    const thread = await prisma.chatThread.findUnique({ where: { id: threadId } });
    if (!thread) {
      return NextResponse.json({ error: "Thread not found" }, { status: 404 });
    }

    const updateData: Record<string, unknown> = {};

    // Update tailored profile
    if (tailoredProfile) {
      updateData.tailoredProfile = typeof tailoredProfile === "string"
        ? tailoredProfile
        : JSON.stringify(tailoredProfile);
    }

    // Delete old PDF from Storage Service before uploading new one
    if (deleteOldPdfKey) {
      try {
        await storageClient.delete(deleteOldPdfKey);
        console.log(`[context] Deleted old PDF: ${deleteOldPdfKey}`);
      } catch (e) {
        console.warn(`[context] Failed to delete old PDF ${deleteOldPdfKey}:`, e);
      }
    }

    // Upload new PDF to Storage Service
    if (pdfData && pdfKey) {
      await storageClient.uploadBase64(pdfKey, pdfData, "application/pdf");
    }

    // Update tex metadata
    if (texKey) updateData.texKey = texKey;
    if (latexHash) updateData.latexHash = latexHash;

    // Update PDF metadata
    if (pdfKey) updateData.pdfKey = pdfKey;
    if (pdfHash) updateData.pdfHash = pdfHash;

    if (Object.keys(updateData).length > 0) {
      await prisma.chatThread.update({
        where: { id: threadId },
        data: updateData,
      });
    }

    return NextResponse.json({
      success: true,
      pdfKey: pdfKey || thread.pdfKey || undefined,
    });
  } catch (error) {
    console.error("Internal Context POST error:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}
