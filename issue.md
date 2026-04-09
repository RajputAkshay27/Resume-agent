# Root Cause Analysis: Chat Creation Races & Session Synchronization Errors

## Issue 1: Duplicate Chat Creation on Delete All
**Symptoms**: When deleting the last chat, two "New Resume Chat" items would appear instead of one.
**Cause**:
- **Side-Effects in React State Updates**: The original `deleteChat` function contained `setTimeout` and `setCurrentSessionId` calls inside the `setSessions` functional update callback.
- **React 19 Strict Mode**: In development, React 19 runs functional updates twice to ensure purity. This caused the `setTimeout` to be scheduled twice per delete.
- **Stale Closures**: The `createNewChat` function captured the old `sessions` list from the render closure, so it didn't "see" that a new chat was already in progress or should be skipped.

## Issue 2: `ValueError: The last_update_time provided...`
**Symptoms**: Backend agent would crash with a "stale session" error from `SqliteSessionService`.
**Cause**:
- **Concurrent Handshakes**: When the frontend double-created chats (see Issue 1), both chats would attempt to hit the Python agent simultaneously for history and state syncing.
- **Optimistic Locking**: ADK's SQLite service uses timestamps to prevent data loss. The multiple concurrent updates for the same session ID (due to rapid creation/deletion) caused one update to be rejected as "stale."

## Issue 3: `Cannot send event type 'RUN_STARTED'` Console Error
**Symptoms**: Red console errors in the browser, often preventing the chat from loading.
**Cause**:
- **Uncaught Runtime Errors**: Earlier versions of the backend (`main.py`) had a missing import (`Response`) inside a route function. This caused a 500 error when the route was hit.
- **CopilotKit State Confussion**: When the backend returns a 500 JSON instead of a stream, the `CopilotRuntime` and `CopilotChat` can enter a "Double Run" state where they try to send multiple `RUN_STARTED` events before previous ones have settled.

---

## The Fix

### 1. Ref-based Semaphore (The "Lock")
We added `creatingRef` in `Dashboard.tsx`. Unlike state, refs update synchronously. This prevents `createNewChatInternal` from being called multiple times in the same execution tick, effectively blocking the "Strict Mode" double-trigger.

### 2. Side-Effect Separation
Moved all logic for switching sessions and ensuring at least one chat exists into standardized `useEffect` hooks. This ensures the UI only reacts to the *final, applied* state of the `sessions` array.

### 3. Backend Stability
Reorganized `agent/main.py` imports to move `Response` to the top level, preventing accidental `ImportError` or `NameError` during runtime requests.

### 4. Diagnostic Logging
Added a standard try/catch block to `api/copilotkit/route.ts`. If the backend fails again, the exact cause will now be logged in the **Server Console** (the `npm run dev:ui` window) instead of just showing as a generic library error in the browser.

## Issue 4: New Session Created on Every Page Refresh
**Symptoms**: Every time the user refreshed the browser, a duplicate "New Resume Chat" was added to the sidebar.
**Cause**:
- **Asynchronous Data Race**: The `Dashboard` component initializes with an empty `sessions` array. The logic that "Ensures at least one chat exists" was triggering immediately upon mount.
- **Loading Gap**: While the initial `fetch("/api/chats")` was in flight, the component saw 0 chats and automatically created a new one. By the time the actual chats arrived, the new session had already been persisted to the DB.

## The Final Fix: `isLoadingSessions` Guard
We introduced an `isLoadingSessions` state variable. The auto-creation logic is now explicitly blocked until the initial database fetch has completed (`isLoadingSessions === false`). This ensures that we only create a new chat if the database *truly* has no history for the user.

## Issue 5: `RUN_ERROR` due to Agent Name Mismatch
**Symptoms**: Even after fixing race conditions, the chat would fail with `Cannot send event type 'RUN_STARTED'`.
**Cause**:
- **String Mismatch**: The Python backend was using `"resume_building_agent"` while the Next.js runtime and Frontend were looking for `"resume_builder_agent"`. 
- **Handshake Failure**: CopilotKit could not find the correctly named agent on the FastAPI backend, causing an immediate abort of the run.

## The Fix: Unified Naming
We standardized the agent name to `resume_builder_agent` across all files (`main.py`, `agent.py`, `route.ts`, and `Dashboard.tsx`).

## Issue 6: Timeout Mismatch & Zombie Responses
**Symptoms**: The frontend (chat UI) would show an error or time out, but the backend Python agent would continue processing and eventually try to save a response to a closed connection.
**Cause**:
- **Default Execution Limits**: Next.js App Router has a default execution limit (often 30s). The `NVIDIA NIM` model, especially the larger Mistral variants, can sometimes take longer than 30 seconds to generate a full response.
- **Connection Severance**: When the frontend times out, the backend doesn't always know immediately, leading to a "zombie" state update that conflicts with the user's next message.

## The Final Fix: Extended Timeout Windows
We increased the `maxDuration` for the Next.js runtime in `route.ts` to 60 seconds and synchronized the Python `uvicorn` backend with a 65-second keep-alive. This gives the model enough time to complete its suggestions and ensures the connection stays robust during long-running tasks.
