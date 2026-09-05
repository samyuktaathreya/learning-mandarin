import { BrowserRouter, Routes, Route } from "react-router-dom"
import DuolingoStyleQuestions from "./pages/DuolingoStylePractice"
import MandarinVoicePractice from "./pages/MandarinVoicePractice"
import TestPronunciation from "./pages/TestPronounciation"
import { useEffect } from 'react';
import { initTurnstile } from './api/client';

function App() { 
  useEffect(() => {
    console.log('[DEBUG] App mounted, checking window.turnstile:', !!window.turnstile);
    if (window.turnstile) {
      initTurnstile();
    } else {
      console.log('[DEBUG] waiting for turnstile script to load');
      const script = document.querySelector('script[src*="turnstile"]');
      console.log('[DEBUG] turnstile script tag found:', !!script);
      script?.addEventListener('load', () => {
        console.log('[DEBUG] turnstile script load event fired');
        initTurnstile();
      });
    }
  }, []);

  return (
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <div 
        id="turnstile-container" 
      />
      <Routes>
        <Route path="/" element={<DuolingoStyleQuestions />} />
        <Route path="/mandarin-voice-practice" element={<MandarinVoicePractice />} />
        <Route path="/test" element={<TestPronunciation />} />
        <Route path="*" element={<div>I am lost! Current path: {window.location.pathname}</div>} />
      </Routes>
    </BrowserRouter>
  )
}

export default App