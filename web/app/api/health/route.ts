import pkg from "../../../package.json";

export function GET(): Response {
  return Response.json({ ok: true, version: pkg.version });
}
