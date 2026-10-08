// Where to send someone after they sign up or log in. Login links arrive by
// email, so the destination is remembered in this browser until the link is
// opened. Only paths on this site are accepted.
const STORAGE_KEY = "postLoginRedirect";

export function safeRedirect(value: unknown): string | null {
  if (typeof value !== "string") return null;
  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return null;
  return value;
}

export function rememberPostLoginRedirect(value: unknown) {
  const destination = safeRedirect(value);
  try {
    if (destination) localStorage.setItem(STORAGE_KEY, destination);
    else localStorage.removeItem(STORAGE_KEY);
  } catch (e) {
    // Storage can be unavailable (private mode); the lobby is the fallback.
  }
}

export function takePostLoginRedirect(): string | null {
  try {
    const destination = safeRedirect(localStorage.getItem(STORAGE_KEY));
    localStorage.removeItem(STORAGE_KEY);
    return destination;
  } catch (e) {
    return null;
  }
}
