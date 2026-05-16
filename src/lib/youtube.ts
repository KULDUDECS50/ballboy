/**
 * Extract a YouTube video ID from the common URL shapes:
 *   https://www.youtube.com/watch?v=ID
 *   https://youtu.be/ID
 *   https://www.youtube.com/embed/ID
 *   https://www.youtube.com/shorts/ID
 * Returns null if no valid 11-char ID is found.
 */
export function parseYouTubeId(input: string): string | null {
  const trimmed = input.trim();
  if (!trimmed) return null;

  // Bare 11-char ID pasted directly.
  if (/^[\w-]{11}$/.test(trimmed)) return trimmed;

  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    return null;
  }

  const host = url.hostname.replace(/^www\./, "");

  if (host === "youtu.be") {
    const id = url.pathname.slice(1).split("/")[0];
    return /^[\w-]{11}$/.test(id) ? id : null;
  }

  if (host === "youtube.com" || host === "m.youtube.com") {
    const v = url.searchParams.get("v");
    if (v && /^[\w-]{11}$/.test(v)) return v;

    const parts = url.pathname.split("/").filter(Boolean);
    // /embed/ID  or  /shorts/ID  or  /live/ID
    if (parts.length >= 2 && ["embed", "shorts", "live"].includes(parts[0])) {
      return /^[\w-]{11}$/.test(parts[1]) ? parts[1] : null;
    }
  }

  return null;
}
