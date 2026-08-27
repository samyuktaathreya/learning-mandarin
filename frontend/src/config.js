// Backend base URL, read from VITE_API_URL in .env. Falls back to '' so
// requests stay relative to the frontend's own origin (e.g. the dev proxy
// in vite.config.js) when the variable isn't set.
export const API_BASE_URL = import.meta.env.VITE_API_URL || '';
