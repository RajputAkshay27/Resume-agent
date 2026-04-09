import { NextRequest, NextResponse } from "next/server";
import { PrismaClient } from "@prisma/client";
import { PrismaBetterSqlite3 } from "@prisma/adapter-better-sqlite3";
import { s3Client, deleteS3Object } from "@/lib/s3";
import { GetObjectCommand, PutObjectCommand } from "@aws-sdk/client-s3";

const BUCKET_NAME = process.env.S3_BUCKET || "resume_agent_bucket";
const globalForPrisma = global as unknown as { prisma: PrismaClient };
let prisma = globalForPrisma.prisma;
if (!prisma) {
  const adapter = new PrismaBetterSqlite3({
    url: process.env.DATABASE_URL || "file:./dev.db"
  });
  prisma = new PrismaClient({ adapter });
  globalForPrisma.prisma = prisma;
}

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
        const response = await s3Client.send(
          new GetObjectCommand({ Bucket: BUCKET_NAME, Key: pdfKey })
        );
        const body = await response.Body?.transformToByteArray();
        if (!body) throw new Error("Empty body");
        return new NextResponse(body as any, {
          headers: {
            "Content-Type": "application/pdf",
            "Content-Disposition": `attachment; filename="resume.pdf"`,
          },
        });
      } catch (error) {
        console.error(`S3 proxy error for ${pdfKey}:`, error);
        return NextResponse.json({ error: "Failed to fetch PDF" }, { status: 404 });
      }
    }

    if (!threadId) {
      return NextResponse.json({ error: "Missing threadId" }, { status: 400 });
    }

    const thread = await prisma.chatThread.findUnique({
      where: { id: threadId },
      include: { user: { select: { masterProfile: true } } },
    });

    if (!thread) {
      return NextResponse.json({ error: "Thread not found" }, { status: 404 });
    }

    // Fetch the template content from S3
    let templateContent: string | null = null;
    if (thread.templateKey) {
      try {
        const response = await s3Client.send(
          new GetObjectCommand({ Bucket: BUCKET_NAME, Key: thread.templateKey })
        );
        if (response.Body) {
          templateContent = await response.Body.transformToString();
        }
      } catch (e) {
        console.error("Failed to fetch template from S3:", e);
      }
    }

    return NextResponse.json({
      jobDescription: thread.jobDescription || null,
      jobUrl: thread.jobUrl || null,
      tailoredProfile: thread.tailoredProfile || null,
      templateKey: thread.templateKey || null,
      templateContent,
      sectionPrefs: thread.sectionPrefs || null,
      masterProfile: thread.user?.masterProfile || null,
      pdfKey: thread.pdfKey || null,
      pdfHash: thread.pdfHash || null,
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

    // Delete old PDF from S3 before uploading new one (requirement #8)
    if (deleteOldPdfKey) {
      try {
        await deleteS3Object(BUCKET_NAME, deleteOldPdfKey);
        console.log(`[context] Deleted old PDF: ${deleteOldPdfKey}`);
      } catch (e) {
        console.warn(`[context] Failed to delete old PDF ${deleteOldPdfKey}:`, e);
      }
    }

    // Upload new PDF to S3
    if (pdfData && pdfKey) {
      const buffer = Buffer.from(pdfData, "base64");
      await s3Client.send(
        new PutObjectCommand({
          Bucket: BUCKET_NAME,
          Key: pdfKey,
          Body: buffer,
          ContentType: "application/pdf",
        })
      );
    }

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
