import { BrowserRouter, Routes, Route } from "react-router-dom"
import DuolingoStyleQuestions from "./pages/DuolingoStylePractice"
import MandarinVoicePractice from "./pages/MandarinVoicePractice"
import TestPronunciation from "./pages/TestPronounciation"
import { useEffect } from 'react';
import { initTurnstile } from './api/client';

function App() { 
  useEffect(() => {
    // Wait for Turnstile script to load
    if (window.turnstile) {
      initTurnstile();
    } else {
      document.querySelector('script[src*="turnstile"]')
        .addEventListener('load', initTurnstile);
    }
  }, []);

  return (
    <BrowserRouter basename={import.meta.env.BASE_URL}>
      <div id="turnstile-container" style={{ display: 'none' }} />
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