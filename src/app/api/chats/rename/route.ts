import { NextRequest, NextResponse } from "next/server";
import { getServerSession } from "next-auth/next";
import { authOptions } from "../../auth/[...nextauth]/route";
import prisma from "@/lib/db";

async function generateTitle(message: string): Promise<string> {
  const apiKey = process.env.RENAME_LLM_API_KEY || process.env.GOOGLE_API_KEY;
  const baseUrl = process.env.RENAME_LLM_BASE_URL || "https://generativelanguage.googleapis.com/v1beta/openai/v1";
  const model = process.env.RENAME_LLM_MODEL || "gemma-3-27b-it";

  if (!apiKey) {
    console.warn("[rename] No API key found for renaming");
    return message.substring(0, 35) + (message.length > 35 ? "..." : "");
  }

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);

    const response = await fetch(`${baseUrl.replace(/\/$/, "")}/chat/completions`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${apiKey}`,
        "Content-Type": "application/json"
      },
      signal: controller.signal,
      body: JSON.stringify({
        model: model,
        messages: [
          {
            role: "user",
            content: `Summarize this message into a concise 3-5 word chat title. Reply with ONLY the title text, no quotes, no punctuation at the end:\n\n${message.substring(0, 800)}`
          }
        ],
        max_tokens: 25,
        temperature: 0.3,
      })
    });

    clearTimeout(timeoutId);

    if (response.ok) {
      const data = await response.json();
      const title = data.choices?.[0]?.message?.content?.trim();
      if (title) {
        console.log(`[rename] AI Generated title: ${title}`);
        return title;
      }
    } else {
      const errorMsg = await response.text();
      console.warn(`[rename] API call failed (${response.status}):`, errorMsg);
    }
  } catch (e) {
    console.warn("[rename] LLM call failed:", e);
  }

  // Final fallback: truncate
  return message.substring(0, 35) + (message.length > 35 ? "..." : "");
}

export async function POST(req: NextRequest) {
  try {
    const session = await getServerSession(authOptions);
    if (!session?.user?.email) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { threadId, message } = await req.json();

    if (!threadId || !message) {
      console.error("[rename] Missing threadId or message in request body", { threadId, message });
      return NextResponse.json({ error: "Missing threadId or message" }, { status: 400 });
    }

    // Robustly handle 'message' if it's an object/array (CopilotKit structure)
    let messageText = "";
    if (typeof message === "string") {
      messageText = message;
    } else if (Array.isArray(message)) {
      messageText = message.map(p => (typeof p === "string" ? p : p?.text || "")).join(" ");
    } else if (typeof message === "object") {
      messageText = message.text || message.content || JSON.stringify(message);
    }

    if (!messageText.trim()) {
      console.warn("[rename] Empty message text after extraction", message);
      return NextResponse.json({ title: "New Resume Chat" });
    }

    // Verify ownership
    const thread = await prisma.chatThread.findUnique({
      where: { id: threadId },
      include: { user: true }
    });

    if (!thread || thread.user.email !== session.user.email) {
      console.error("[rename] Thread not found or ownership mismatch", { threadId, user: session.user.email });
      return NextResponse.json({ error: "Unauthorized" }, { status: 403 });
    }

    console.log(`[rename] Processing thread ${threadId} using message: "${messageText.substring(0, 50)}..."`);
    let newTitle = await generateTitle(messageText);

    // Sanitize: remove stray quotes, braces, "title:" prefix
    newTitle = newTitle.replace(/["'{}[\]]/g, "").replace(/^title:\s*/i, "").trim();
    if (!newTitle) newTitle = messageText.substring(0, 30);

    await prisma.chatThread.update({
      where: { id: threadId },
      data: { 
        title: newTitle,
        updatedAt: new Date() // Force timestamp update so it jumps to top in UI
      }
    });

    console.log(`[rename] Thread ${threadId} successfully renamed to: "${newTitle}"`);

    console.log(`[rename] Thread ${threadId} → "${newTitle}"`);
    return NextResponse.json({ title: newTitle });
  } catch (error) {
    console.error("[rename] Fatal error:", error);
    return NextResponse.json({
      title: "New Chat",
      error: "Error updating title"
    }, { status: 500 });
  }
}
