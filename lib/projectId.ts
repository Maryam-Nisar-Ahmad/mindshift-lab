/**
 * Next.js [id] params are usually decoded once. If a link was double-encoded,
 * the param can look like "10%20-%20video" while the disk folder is "10 - video".
 * Normalize to the real folder id, then encode once for URLs.
 */
export function normalizeProjectId(raw: string): string {
  if (!raw) return raw;
  let out = raw.trim();
  try {
    let prev = '';
    for (let i = 0; i < 8 && out !== prev; i++) {
      prev = out;
      if (!/%[0-9A-Fa-f]{2}/i.test(out)) break;
      const next = decodeURIComponent(out);
      if (next === out) break;
      out = next;
    }
  } catch {
    return raw.trim();
  }
  return out;
}

export function projectIdFromParams(raw: string | string[] | undefined): string {
  const s = Array.isArray(raw) ? raw[0] ?? '' : raw ?? '';
  return normalizeProjectId(s);
}

/** One path segment for /api/projects/{id}/... */
export function encodeProjectIdForPath(id: string): string {
  return encodeURIComponent(normalizeProjectId(id));
}

/** One segment for /project/[id] in router.push */
export function encodeProjectIdForRoute(id: string): string {
  return encodeURIComponent(normalizeProjectId(id));
}
