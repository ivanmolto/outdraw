// A stand-in for Upstash's REST API for the Playwright suite, so the real proxy.ts and
// @upstash/ratelimit run against it through UPSTASH_REDIS_REST_URL. It answers the sliding
// window's EVALSHA (single commands and auto-pipelined batches) from fake-redis.mjs.
import { createServer } from "node:http";

import { createFakeRedis } from "../src/server/fake-redis.mjs";

const PORT = Number(process.env.MOCK_REDIS_PORT ?? 8788);
const TOKEN = process.env.UPSTASH_REDIS_REST_TOKEN ?? "e2e-redis-token";

const redis = createFakeRedis();

/** @param {unknown[]} command `["evalsha", sha, numkeys, ...keys, ...args]` */
function exec(command) {
  const [name, , numkeys, ...rest] = command;
  if (name !== "evalsha" && name !== "eval") return { error: `ERR unsupported ${name}` };
  const n = Number(numkeys);
  return { result: redis.limit(rest.slice(0, n).map(String), rest.slice(n)) };
}

createServer((req, res) => {
  const send = (status, body) => {
    res.writeHead(status, { "content-type": "application/json" });
    res.end(JSON.stringify(body));
  };
  if (req.method === "GET" && req.url === "/health") return send(200, { ok: true });
  if (req.headers.authorization !== `Bearer ${TOKEN}`) return send(401, { error: "Unauthorized" });
  let raw = "";
  req.on("data", (chunk) => (raw += chunk));
  req.on("end", () => {
    const body = JSON.parse(raw || "[]");
    if (req.url === "/pipeline") return send(200, body.map(exec));
    send(200, exec(body));
  });
}).listen(PORT, "127.0.0.1", () => console.log(`mock redis on :${PORT}`));
