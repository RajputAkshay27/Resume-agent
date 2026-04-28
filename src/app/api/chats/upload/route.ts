import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth/next";
import prisma from "@/lib/db";
import { authOptions } from "../../auth/[...nextauth]/route";
import { storageClient } from "@/lib/storage-client";

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

    // Create unique key specific to this thread
    const uniqueSuffix = `${Date.now()}-${Math.round(Math.random() * 1e9)}`;
    const originalName = file.name || "template.tex";
    const objectKey = `${session.user.email}/${threadId}-${uniqueSuffix}-${originalName}`;

    // Upload to Storage Service
    await storageClient.upload(file, objectKey);

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
