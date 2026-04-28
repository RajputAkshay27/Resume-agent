import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth/next";
import prisma from "@/lib/db";
import { authOptions } from "../auth/[...nextauth]/route";

export async function GET() {
  try {
    const session = await getServerSession(authOptions);
    if (!session?.user?.email) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const user = await prisma.user.findUnique({
      where: { email: session.user.email },
      select: { templateKey: true, masterProfile: true }
    });

    return NextResponse.json({ 
      templateKey: user?.templateKey || null,
      hasMasterProfile: !!user?.masterProfile
    });
  } catch (error) {
    console.error("GET /api/user error:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const session = await getServerSession(authOptions);
    if (!session?.user?.email) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { templateKey } = await req.json();

    // Fetch current user to get old key
    const currentUser = await prisma.user.findUnique({
      where: { email: session.user.email },
      select: { templateKey: true }
    });

    // Delete old template from Storage Service if different
    if (currentUser?.templateKey && currentUser.templateKey !== templateKey) {
      try {
        const { storageClient } = await import("@/lib/storage-client");
        await storageClient.delete(currentUser.templateKey);
      } catch (e) {
        console.warn("Failed to delete old template:", e);
      }
    }

    const user = await prisma.user.update({
      where: { email: session.user.email },
      data: { templateKey }
    });

    return NextResponse.json({ success: true, templateKey: user.templateKey });
  } catch (error) {
    console.error("POST /api/user error:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}
