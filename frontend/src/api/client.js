const TURNSTILE_SITE_KEY = import.meta.env.VITE_TURNSTILE_SITE_KEY;

let currentToken = null;
let turnstileRendered = false;
let tokenResolvers = [];

export function initTurnstile() {
  console.log('[DEBUG] initTurnstile called, turnstileRendered:', turnstileRendered);
  if (turnstileRendered) return;
  if (!window.turnstile) {
    console.log('[DEBUG] window.turnstile not available yet');
    return;
  }

  const container = document.getElementById('turnstile-container');
  console.log('[DEBUG] container element:', container);
  console.log('[DEBUG] container dimensions:', container?.getBoundingClientRect());
  console.log('[DEBUG] container computed style:', container ? getComputedStyle(container).display : 'N/A');

  turnstileRendered = true;
  const widgetId = window.turnstile.render('#turnstile-container', {
    sitekey: TURNSTILE_SITE_KEY,
    callback: (token) => {
      console.log('[DEBUG] Turnstile callback fired, token length:', token.length);
      currentToken = token;
      tokenResolvers.forEach(resolve => resolve(token));
      tokenResolvers = [];
    },
    'expired-callback': () => { console.log('[DEBUG] Turnstile expired'); currentToken = null; },
    'error-callback': (err) => { console.log('[DEBUG] Turnstile error-callback fired:', err); currentToken = null; },
  });
  console.log('[DEBUG] widget rendered with id:', widgetId);
}

function getToken() {
  console.log('[DEBUG] getToken called, currentToken exists:', !!currentToken);
  if (currentToken) return Promise.resolve(currentToken);
  return new Promise((resolve, reject) => {
    tokenResolvers.push(resolve);
    console.log('[DEBUG] waiting for token, resolvers queued:', tokenResolvers.length);
    setTimeout(() => {
      console.log('[DEBUG] token wait timed out after 10s');
      reject(new Error('Turnstile token timeout'));
    }, 10000);
  });
}

export async function apiFetch(url, options = {}) {
  let token = null;
  try {
    token = await getToken();
  } catch (e) {
    console.log('[DEBUG] apiFetch failed to get token:', e.message);
  }

  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { 'X-Turnstile-Token': token } : {}),
    ...options.headers,
  };

  // consume the token — force a fresh one for the next call
  currentToken = null;
  if (window.turnstile && turnstileRendered) {
    window.turnstile.reset('#turnstile-container');
  }

  return fetch(url, { ...options, headers });
}