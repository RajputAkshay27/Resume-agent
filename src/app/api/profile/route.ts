import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth/next";
import { PrismaClient } from "@prisma/client";
import { PrismaBetterSqlite3 } from "@prisma/adapter-better-sqlite3";
import { authOptions } from "../auth/[...nextauth]/route";

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
      where: { email: session.user.email },
      select: { masterProfile: true }
    });

    if (!user) return NextResponse.json({ error: "User not found" }, { status: 404 });

    return NextResponse.json({
      masterProfile: user.masterProfile ? JSON.parse(user.masterProfile) : null,
    });
  } catch (error) {
    console.error("GET /api/profile error:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  try {
    const session = await getServerSession(authOptions);
    if (!session?.user?.email) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { masterProfile } = await req.json();

    if (!masterProfile || typeof masterProfile !== "object") {
      return NextResponse.json({ error: "Invalid master profile data" }, { status: 400 });
    }

    await prisma.user.update({
      where: { email: session.user.email },
      data: { masterProfile: JSON.stringify(masterProfile) },
    });

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("POST /api/profile error:", error);
    return NextResponse.json({ error: "Internal Server Error" }, { status: 500 });
  }
}
