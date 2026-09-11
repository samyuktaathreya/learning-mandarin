import { SignedIn, SignedOut, SignIn, SignUp, RedirectToSignIn, useAuth, UserButton } from '@clerk/clerk-react';
import { Navigate, Link } from 'react-router-dom';
import { useEffect } from 'react';
import { registerTokenGetter } from '../api/client';

/**
 * Bridges Clerk's getToken() into api/client.js so apiFetch can attach
 * the bearer token without needing hooks itself. Mount once, inside ClerkProvider.
 */
export function TokenBridge() {
  const { getToken } = useAuth();
  useEffect(() => { registerTokenGetter(getToken); }, [getToken]);
  return null;
}

/**
 * Wraps routes that require authentication.
 * Redirects unauthenticated users to the sign-in page.
 */
export function ProtectedRoute({ children }) {
  return (
    <>
      <SignedIn>{children}</SignedIn>
      <SignedOut>
        <RedirectToSignIn replace />
      </SignedOut>
    </>
  );
}

/**
 * Rendered at /sign-in/*.
 * Shows the Clerk SignIn UI if signed out, or redirects home if already signed in.
 */
export function SignInPage() {
  return (
    <>
      <SignedIn>
        <Navigate to="/" replace />
      </SignedIn>
      <SignedOut>
        <div style={{ display: 'flex', justifyContent: 'center', marginTop: '3rem' }}>
          <SignIn routing="path" path="/sign-in" signUpUrl="/sign-up" />
        </div>
      </SignedOut>
    </>
  );
}

/**
 * Rendered at /sign-up/*.
 * Shows the Clerk SignUp UI if signed out, or redirects home if already signed in.
 */
export function SignUpPage() {
  return (
    <>
      <SignedIn>
        <Navigate to="/" replace />
      </SignedIn>
      <SignedOut>
        <div style={{ display: 'flex', justifyContent: 'center', marginTop: '3rem' }}>
          <SignUp routing="path" path="/sign-up" signInUrl="/sign-in" />
        </div>
      </SignedOut>
    </>
  );
}

/**
 * Persistent, non-blocking banner shown to guests reminding them
 * progress isn't saved. Rendered inside App's layout, not gating routes.
 */
export function GuestBanner() {
  return (
    <SignedOut>
      <div style={{
        background: '#fff3cd',
        color: '#664d03',
        padding: '0.5rem 1rem',
        textAlign: 'center',
        fontSize: '0.9rem',
      }}>
        You're using this as a guest — your progress won't be saved.{' '}
        <Link to="/sign-up">Create an account</Link> to keep it.
      </div>
    </SignedOut>
  );
}

export function AccountControl() {
  return (
    <>
      <SignedIn>
        <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '0.5rem 1rem' }}>
          <UserButton afterSignOutUrl="/" />
        </div>
      </SignedIn>
      <SignedOut>
        <GuestBanner />
      </SignedOut>
    </>
  );
}