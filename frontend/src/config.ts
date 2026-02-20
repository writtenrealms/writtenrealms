const googleClientId = (import.meta.env.VITE_GOOGLE_CLIENT_ID || "").trim();

export const API_BASE = import.meta.env.VITE_API_BASE;
export const FORGE_WS_URI = import.meta.env.VITE_FORGE_WS_URI;
export const GOOGLE_CLIENT_ID = googleClientId;
export const GOOGLE_AUTH_ENABLED = GOOGLE_CLIENT_ID.length > 0;

export const INTRO_WORLD_ID = "217";
export const MAP_CONFIG = {
  UNIT: 8
};
