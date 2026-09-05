import { BrowserRouter, Routes, Route } from "react-router-dom"
import { useEffect, useState } from 'react';
import DuolingoStyleQuestions from "./pages/DuolingoStylePractice"
import MandarinVoicePractice from "./pages/MandarinVoicePractice"
import TestPronunciation from "./pages/TestPronounciation"
import { initTurnstile, verifySession } from './api/client';

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
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <div id="turnstile-container" />
      {error && <div>Verification failed: {error}</div>}
      {!ready && !error && <div>Loading…</div>}
      {ready && (
        <Routes>
          <Route path="/" element={<DuolingoStyleQuestions />} />
          <Route path="/mandarin-voice-practice" element={<MandarinVoicePractice />} />
          <Route path="/test" element={<TestPronunciation />} />
          <Route path="*" element={<div>I am lost! Current path: {window.location.pathname}</div>} />
        </Routes>
      )}
    </BrowserRouter>
  );
}

export default App