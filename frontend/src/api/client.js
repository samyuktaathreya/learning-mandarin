const TURNSTILE_SITE_KEY = import.meta.env.VITE_TURNSTILE_SITE_KEY;

let currentToken = null;
let turnstileRendered = false;
let tokenResolvers = [];  // waiting callers

export function initTurnstile() {
  if (turnstileRendered) return;
  if (!window.turnstile) return;

  console.log('Turnstile sitekey:', TURNSTILE_SITE_KEY);  // confirm key is present
  turnstileRendered = true;
  window.turnstile.render('#turnstile-container', {
    sitekey: TURNSTILE_SITE_KEY,
    callback: (token) => {
      console.log('Turnstile token received:', token.slice(0, 20) + '...');
      currentToken = token;
      tokenResolvers.forEach(resolve => resolve(token));
      tokenResolvers = [];
    },
    'expired-callback': () => { console.log('Turnstile token expired'); currentToken = null; },
    'error-callback': (err) => { console.log('Turnstile error:', err); currentToken = null; },
  });
}

function getToken() {
  if (currentToken) return Promise.resolve(currentToken);
  // wait up to 10 seconds for token
  return new Promise((resolve, reject) => {
    tokenResolvers.push(resolve);
    setTimeout(() => reject(new Error('Turnstile token timeout')), 10000);
  });
}

export async function apiFetch(url, options = {}) {
  let token = null;
  try {
    token = await getToken();
  } catch (e) {
    console.warn('Could not get Turnstile token:', e.message);
  }

  return fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'X-Turnstile-Token': token } : {}),
      ...options.headers,
    },
  });
}