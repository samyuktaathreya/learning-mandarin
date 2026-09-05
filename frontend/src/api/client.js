const TURNSTILE_SITE_KEY = import.meta.env.VITE_TURNSTILE_SITE_KEY;

let currentToken = null;
let turnstileRendered = false;
let tokenResolvers = [];
let tokenUseCount = 0;

function log(...args) {
  console.log(`[${Date.now() % 100000}]`, ...args);
}

export function initTurnstile() {
  log('initTurnstile called, turnstileRendered:', turnstileRendered);
  if (turnstileRendered) return;
  if (!window.turnstile) return;

  turnstileRendered = true;
  window.turnstile.render('#turnstile-container', {
    sitekey: TURNSTILE_SITE_KEY,
    callback: (token) => {
      log('CALLBACK fired, token (first 15 chars):', token.slice(0, 15), 'resolvers waiting:', tokenResolvers.length);
      currentToken = token;
      const resolvers = tokenResolvers;
      tokenResolvers = [];
      resolvers.forEach(resolve => resolve(token));
    },
    'expired-callback': () => { log('EXPIRED callback fired'); currentToken = null; },
    'error-callback': (err) => { log('ERROR callback fired:', err); currentToken = null; },
  });
}

function getToken() {
  const callId = Math.random().toString(36).slice(2, 7);
  log(`getToken[${callId}] called, currentToken exists:`, !!currentToken);
  if (currentToken) {
    log(`getToken[${callId}] returning cached token immediately`);
    return Promise.resolve(currentToken);
  }
  return new Promise((resolve, reject) => {
    tokenResolvers.push(resolve);
    log(`getToken[${callId}] queued, total resolvers now:`, tokenResolvers.length);
    setTimeout(() => {
      log(`getToken[${callId}] TIMED OUT after 10s`);
      reject(new Error('Turnstile token timeout'));
    }, 10000);
  });
}

export async function apiFetch(url, options = {}) {
  log('apiFetch START for url:', url);
  let token = null;
  try {
    token = await getToken();
    tokenUseCount++;
    log('apiFetch got token for', url, '- use #', tokenUseCount, '- token first 15 chars:', token?.slice(0, 15));
  } catch (e) {
    log('apiFetch FAILED to get token for', url, ':', e.message);
  }

  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { 'X-Turnstile-Token': token } : {}),
    ...options.headers,
  };

  return fetch(url, { ...options, headers });
}