import { PrismaClient } from "@prisma/client";
import { PrismaBetterSqlite3 } from "@prisma/adapter-better-sqlite3";

const globalForPrisma = global as unknown as { prisma: PrismaClient };

let prisma: PrismaClient;

if (process.env.NODE_ENV === "production") {
  // Hardcode the absolute production path to bypass Webpack blindly inlining build-time env vars
  const adapter = new PrismaBetterSqlite3({
    url: "/app/data/dev.db"
  });
  prisma = new PrismaClient({ adapter });
} else {
  if (!globalForPrisma.prisma) {
    const dbUrl = process.env.DATABASE_URL || "file:./dev.db";
    const adapter = new PrismaBetterSqlite3({
      url: dbUrl.replace(/^file:(?:\/\/)?/, "")
    });
    globalForPrisma.prisma = new PrismaClient({ adapter });
  }
  prisma = globalForPrisma.prisma;
}

export default prisma;
