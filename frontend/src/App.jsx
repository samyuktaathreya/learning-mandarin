import { BrowserRouter, Routes, Route } from "react-router-dom";
import { useEffect, useState } from 'react';
import DuolingoStyleQuestions from "./pages/DuolingoStylePractice";
import MandarinVoicePractice from "./pages/MandarinVoicePractice";
import TestPronunciation from "./pages/TestPronounciation";
import { initTurnstile, verifySession } from './api/client';
import { TokenBridge, SignInPage, SignUpPage, GuestBanner } from './Components/Auth';
import { ClerkProvider } from '@clerk/clerk-react';

const PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;

if (!PUBLISHABLE_KEY) {
  throw new Error("Missing Publishable Key. Ensure VITE_CLERK_PUBLISHABLE_KEY is set in your .env file.");
}

function App() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const start = () => {
      initTurnstile();
      verifySession()
        .then(() => setReady(true))
        .catch(err => { console.error(err); setError(err.message); });
    };

    if (window.turnstile) {
      start();
    } else {
      const script = document.querySelector('script[src*="turnstile"]');
      script?.addEventListener('load', start);
    }
  }, []);

  return (
    <ClerkProvider publishableKey={PUBLISHABLE_KEY} afterSignOutUrl="/">
      <TokenBridge />
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <div id="turnstile-container" style={{ display: 'none' }}/>
        {error && <div>Verification failed: {error}</div>}
        {!ready && !error && <div>Loading…</div>}
        {ready && (
          <>
            <GuestBanner />
            <Routes>
              <Route path="/sign-in/*" element={<SignInPage />} />
              <Route path="/sign-up/*" element={<SignUpPage />} />

              {/* Guests allowed now — no ProtectedRoute wrapper */}
              <Route path="/" element={<DuolingoStyleQuestions />} />
              <Route path="/mandarin-voice-practice" element={<MandarinVoicePractice />} />
              <Route path="/test" element={<TestPronunciation />} />

              <Route path="*" element={<div>I am lost! Current path: {window.location.pathname}</div>} />
            </Routes>
          </>
        )}
      </BrowserRouter>
    </ClerkProvider>
  );
}

export default App;