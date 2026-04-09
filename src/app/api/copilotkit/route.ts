import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { HttpAgent } from "@ag-ui/client";
import { NextRequest } from "next/server";

// Fix UND_ERR_BODY_TIMEOUT: undici (used internally by Node.js fetch) has a
// default 30s body timeout which is too short for long AI generation runs.
// We patch it via the undici module that ships with Node.js itself.
// IMPORTANT: also set NODE_OPTIONS=--no-experimental-fetch in .env.local to
// force Next.js to use http.request which has no body timeout, OR set
// UNDICI_TIMEOUT_BODY=0 to disable it at the undici level.
// The actual env-based fix is in .env.local — this file just sets maxDuration.
export const maxDuration = 600; // 10 minutes — allow long AI generation sessions

// 1. You can use any service adapter here for multi-agent support. We use
//    the empty adapter since we're only using one agent.
const serviceAdapter = new ExperimentalEmptyAdapter();

// 2. Create the CopilotRuntime instance and utilize the AG-UI client
//    to setup the connection with the ADK agent.
const runtime = new CopilotRuntime({
  agents: {
    // Our FastAPI endpoint URL
    resume_builder_agent: new HttpAgent({ url: process.env.AGENT_URL || "http://localhost:8000/" }) as any,
  },
});

// 3. Build a Next.js API route that handles the CopilotKit runtime requests.
export const POST = async (req: NextRequest) => {
  try {
    const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
      runtime,
      serviceAdapter,
      endpoint: "/api/copilotkit",
    });

    return await handleRequest(req);
  } catch (error) {
    console.error("[CopilotKit] Runtime Error:", error);
    return new Response(JSON.stringify({ error: "Internal Server Error", details: (error as Error).message }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }
};
