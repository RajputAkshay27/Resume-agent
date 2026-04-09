/**
 * Minimal type stubs for `undici` which is bundled with Node.js 18+ but not
 * necessarily listed as a direct npm dependency. These stubs satisfy TypeScript
 * without requiring `npm install undici`.
 */
declare module "undici" {
  export class Agent {
    constructor(options?: {
      bodyTimeout?: number;
      headersTimeout?: number;
      keepAliveTimeout?: number;
      keepAliveMaxTimeout?: number;
      [key: string]: unknown;
    });
  }
  export function setGlobalDispatcher(dispatcher: Agent): void;
}
