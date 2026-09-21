import { API_BASE_URL } from '../config.js';

const TURNSTILE_SITE_KEY = import.meta.env.VITE_TURNSTILE_SITE_KEY;

let currentToken = null;
let turnstileRendered = false;
let tokenResolvers = [];

// --- Clerk bridge ---
let getClerkTokenFn = null;
export function registerTokenGetter(fn) {
  getClerkTokenFn = fn;
}

async function getClerkToken() {
  if (!getClerkTokenFn) return null;
  try {
    return await getClerkTokenFn(); // null if signed out, string if signed in
  } catch {
    return null;
  }
}

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
  const doFetch = async () => {
    const clerkToken = await getClerkToken(); // null for guests, fine
    return fetch(url, {
      ...options,
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(clerkToken ? { Authorization: `Bearer ${clerkToken}` } : {}),
        ...options.headers,
      },
    });
  };

  let res = await doFetch();
  if (res.status === 401) {
    await verifySession();   // gets fresh Turnstile token, POSTs /api/session
    res = await doFetch();
  }
  return res;
}

export async function verifySession() {
  const token = await getToken();
  window.turnstile.reset();
  currentToken = null;
  const res = await fetch(`${API_BASE_URL}/api/auth/verify`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'X-Turnstile-Token': token },
  });
  if (!res.ok) throw new Error('Verification failed');
}