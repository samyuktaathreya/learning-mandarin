import { SignedIn, SignedOut, SignIn, RedirectToSignIn } from '@clerk/clerk-react';
import { Navigate } from 'react-router-dom';

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
          <SignIn routing="path" path="/sign-in" />
        </div>
      </SignedOut>
    </>
  );
}