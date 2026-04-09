/**
 * Next.js Instrumentation Hook
 *
 * This file runs once when the Next.js server starts, before any routes are
 * handled. We use it to patch undici's global dispatcher to disable timeouts,
 * which fixes the UND_ERR_BODY_TIMEOUT error that occurs when the AI agent
 * takes longer than undici's default 30s body timeout to stream its response.
 *
 * See: https://nextjs.org/docs/app/building-your-application/optimizing/instrumentation
 */
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    // undici is bundled with Node.js 18+ and used internally by Next.js fetch.
    // We patch the global dispatcher to remove body/header timeouts so that
    // long-running AI agent responses are never cut off mid-stream.
    const { setGlobalDispatcher, Agent } = await import("undici");

    setGlobalDispatcher(
      new Agent({
        bodyTimeout: 0,         // disable body read timeout (default: 300s in Node, 30s in undici)
        headersTimeout: 0,      // disable headers wait timeout
        keepAliveTimeout: 600_000, // 10 minute keep-alive for long agent runs
        keepAliveMaxTimeout: 600_000,
      })
    );

    console.log("[instrumentation] undici global dispatcher patched: bodyTimeout=0, headersTimeout=0");
  }
}
