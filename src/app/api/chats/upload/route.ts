import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth/next";
import { PrismaClient } from "@prisma/client";
import { PrismaBetterSqlite3 } from "@prisma/adapter-better-sqlite3";
import { authOptions } from "../../auth/[...nextauth]/route";
import { s3Client, ensureBucketExists } from "@/lib/s3";
import { PutObjectCommand } from "@aws-sdk/client-s3";

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

export async function POST(req: NextRequest) {
  try {
    const session = await getServerSession(authOptions);
    if (!session?.user?.email) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const formData = await req.formData();
    const file = formData.get("file") as File;
    const threadId = formData.get("threadId") as string;

    if (!file || !threadId) {
      return NextResponse.json({ error: "Missing file or threadId" }, { status: 400 });
    }

    // Verify user owns this chat
    const user = await prisma.user.findUnique({ where: { email: session.user.email } });
    if (!user) return NextResponse.json({ error: "User not found" }, { status: 404 });

    const thread = await prisma.chatThread.findUnique({ where: { id: threadId } });
    if (!thread || thread.userId !== user.id) {
      return NextResponse.json({ error: "Unauthorized or thread not found" }, { status: 403 });
    }

    // Ensure bucket exists
    await ensureBucketExists(BUCKET_NAME);

    // Convert file to buffer
    const arrayBuffer = await file.arrayBuffer();
    const buffer = Buffer.from(arrayBuffer);

    // Create unique key specific to this thread
    const uniqueSuffix = `${Date.now()}-${Math.round(Math.random() * 1e9)}`;
    const originalName = file.name || "template.tex";
    const objectKey = `${session.user.email}/${threadId}-${uniqueSuffix}-${originalName}`;

    // Upload to S3
    await s3Client.send(
      new PutObjectCommand({
        Bucket: BUCKET_NAME,
        Key: objectKey,
        Body: buffer,
        ContentType: file.type || "application/x-tex",
      })
    );

    // Update the thread record
    await prisma.chatThread.update({
      where: { id: threadId },
      data: { templateKey: objectKey }
    });

    return NextResponse.json({ key: objectKey });
  } catch (error) {
    console.error("Chat upload error:", error);
    return NextResponse.json({ error: "Upload failed" }, { status: 500 });
  }
}
