// An in-memory stand-in for the one Redis command @upstash/ratelimit's sliding window sends:
// EVALSHA of its single-region limit script. `limit` is that Lua script in JS (same keys,
// args and `[remaining, limit]` reply), so ratelimit.test.ts and the Playwright suite run
// the real library against it with no Upstash database.

/** @typedef {{ value: number, expiresAt: number }} Entry */

/** @param {() => number} [now] */
export function createFakeRedis(now = Date.now) {
  /** @type {Map<string, Entry>} */
  const store = new Map();

  /** @param {string} key */
  const get = (key) => {
    const entry = store.get(key);
    if (!entry || entry.expiresAt <= now()) return 0;
    return entry.value;
  };

  /**
   * @param {string[]} keys `[currentKey, previousKey]`
   * @param {(string | number)[]} args `[tokens, now, windowMs, incrementBy]`
   * @returns {[number, number]}
   */
  function limit(keys, args) {
    const [currentKey, previousKey] = keys;
    const [tokens, at, window, incrementBy] = args.map(Number);
    const current = get(currentKey);
    const previous = Math.floor((1 - (at % window) / window) * get(previousKey));
    if (incrementBy > 0 && previous + current >= tokens) return [-1, tokens];
    const value = current + incrementBy;
    const expiresAt = current === 0 ? now() + window * 2 + 1000 : store.get(currentKey).expiresAt;
    store.set(currentKey, { value, expiresAt });
    return [tokens - (value + previous), tokens];
  }

  return {
    store,
    limit,
    /** The subset of `@upstash/redis`'s client the limiter calls. */
    client: {
      /** @param {string} _sha @param {string[]} keys @param {(string | number)[]} args */
      evalsha: async (_sha, keys, args) => limit(keys, args),
      /** @param {string} _script @param {string[]} keys @param {(string | number)[]} args */
      eval: async (_script, keys, args) => limit(keys, args),
    },
  };
}
