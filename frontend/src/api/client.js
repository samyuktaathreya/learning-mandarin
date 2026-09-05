const TURNSTILE_SITE_KEY = import.meta.env.VITE_TURNSTILE_SITE_KEY;

let currentToken = null;
let turnstileRendered = false;

export function initTurnstile() {
  if (turnstileRendered) return;  // guard against double-render
  if (!window.turnstile) return;
  
  turnstileRendered = true;
  window.turnstile.render('#turnstile-container', {
    sitekey: TURNSTILE_SITE_KEY,
    callback: (token) => { currentToken = token; },
    'expired-callback': () => { currentToken = null; },
    'error-callback': () => { currentToken = null; },
  });
}

export async function apiFetch(url, options = {}) {
  return fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(currentToken ? { 'X-Turnstile-Token': currentToken } : {}),
      ...options.headers,
    },
  });
}