import { NextResponse } from "next/server";
import { getServerSession } from "next-auth/next";
import { PrismaClient } from "@prisma/client";
import { PrismaBetterSqlite3 } from "@prisma/adapter-better-sqlite3";
import { authOptions } from "../auth/[...nextauth]/route";
import { storageClient } from "@/lib/storage-client";

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

export async function GET() {
  try {
    const session = await getServerSession(authOptions);
    if (!session?.user?.email) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const user = await prisma.user.findUnique({
      where: { email: session.user.email }
    });

    if (!user) return NextResponse.json({ error: "User not found" }, { status: 404 });

    const threads = await prisma.chatThread.findMany({
      where: { userId: user.id },
      orderBy: { updatedAt: "desc" }
    });

    return NextResponse.json(threads);
  } catch (error) {
    console.error("GET /api/chats error:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}

export async function POST(req: Request) {
  try {
    const session = await getServerSession(authOptions);
    if (!session?.user?.email) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { id, title, jobDescription, jobUrl, sectionPrefs } = await req.json();

    const user = await prisma.user.findUnique({
      where: { email: session.user.email }
    });

    if (!user) return NextResponse.json({ error: "User not found" }, { status: 404 });

    // Try to find if it exists
    let thread = await prisma.chatThread.findUnique({ where: { id } });
    
    if (thread) {
      // Just update it
      const updateData: Record<string, unknown> = { title, updatedAt: new Date() };
      if (jobDescription !== undefined) updateData.jobDescription = jobDescription;
      if (jobUrl !== undefined) updateData.jobUrl = jobUrl;
      if (sectionPrefs !== undefined) {
        updateData.sectionPrefs = typeof sectionPrefs === "string"
          ? sectionPrefs
          : JSON.stringify(sectionPrefs);
      }

      thread = await prisma.chatThread.update({
        where: { id },
        data: updateData
      });
    } else {
      // Create new — copy user's template to a thread-specific key
      let finalTemplateKey: string | null = null;

      if (user.templateKey) {
        const threadTemplateKey = `${user.email}/${id}-template.tex`;
        try {
          await storageClient.copy(user.templateKey, threadTemplateKey);
          finalTemplateKey = threadTemplateKey;
        } catch (e) {
          console.error("Failed to copy template:", e);
          return NextResponse.json({ error: "Failed to initialize template" }, { status: 500 });
        }
      }

      // Check if user has a master profile
      if (!user.masterProfile) {
        return NextResponse.json({ error: "MISSING_MASTER_PROFILE" }, { status: 400 });
      }

      thread = await prisma.chatThread.create({
        data: {
          id,
          title,
          userId: user.id,
          templateKey: finalTemplateKey,
          jobDescription,
          jobUrl,
          sectionPrefs: sectionPrefs
            ? (typeof sectionPrefs === "string" ? sectionPrefs : JSON.stringify(sectionPrefs))
            : null,
        }
      });
    }

    return NextResponse.json(thread);
  } catch (error) {
    console.error("POST /api/chats error:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}

export async function DELETE(req: Request) {
  try {
    const session = await getServerSession(authOptions);
    if (!session?.user?.email) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { searchParams } = new URL(req.url);
    const id = searchParams.get('id');
    
    if (!id) {
      return NextResponse.json({ error: "Missing chat id" }, { status: 400 });
    }

    const user = await prisma.user.findUnique({
      where: { email: session.user.email }
    });

    if (!user) return NextResponse.json({ error: "User not found" }, { status: 404 });

    const thread = await prisma.chatThread.findUnique({ where: { id } });
    if (!thread || thread.userId !== user.id) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 403 });
    }

    // Cleanup Storage Service
    try {
      if (thread.templateKey) {
        await storageClient.delete(thread.templateKey);
      }
      if (thread.pdfKey) {
        await storageClient.delete(thread.pdfKey);
      }
    } catch (e) {
      console.warn(`Failed to cleanup storage objects for thread ${id}:`, e);
    }

    await prisma.chatThread.delete({ where: { id } });

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("DELETE /api/chats error:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}
