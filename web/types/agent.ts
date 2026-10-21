// Response shapes and error reasons shared by the server client (src/server/agent.ts) and
// the browser client (src/lib/api-client.ts). Types only: nothing here runs anywhere.
//
// Generated from the agent's routes: `make openapi` in /agent writes shared/openapi.json and
// `npm run gen:api` turns it into types/api.ts (CI fails when either drifts). Each name
// below is the Pydantic response model in agent/draw/api/{venues,runs}.py.
import type { components } from "./api";

type Schema<K extends keyof components["schemas"]> = components["schemas"][K];

export type VenueId = Schema<"VenueId">;
/** `query` is an opaque key: echo it back to confirm as-is. */
export type ResolutionItem = Schema<"ResolutionItem">;
export type EntityCandidate = Schema<"EntityCandidate">;
export type Resolutions = Schema<"Resolutions">;
export type VenueOut = Schema<"VenueOut">;
export type RunOut = Schema<"RunOut">;
export type CandidateEvidence = Schema<"Evidence">;
export type QlooCall = Schema<"QlooCall">;

/** What the UI may branch on; never a backend message, URL or stack trace. */
export type Reason =
  | "invalid_input"
  | "not_found"
  | "conflict"
  | "budget"
  | "busy"
  | "live_mode_off"
  | "unavailable"
  | "unknown";
