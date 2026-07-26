import { BrowserRouter, Routes, Route } from "react-router-dom"
import DuolingoStyleQuestions from "./pages/DuolingoStylePractice"
import MandarinVoicePractice from "./pages/MandarinVoicePractice"
import TestPronunciation from "./pages/TestPronounciation"
import DebugGrammarTips from "./pages/DebugGrammarTips"

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/duolingo-style-practice" element={<DuolingoStyleQuestions />} />
        <Route path="/mandarin-voice-practice" element={<MandarinVoicePractice />} />
        <Route path="/test" element={<TestPronunciation />} />
        <Route path="/debug-grammar-tips" element={<DebugGrammarTips />} />
        <Route path="*" element={<div>I am lost! Current path: {window.location.pathname}</div>} />
      </Routes>
    </BrowserRouter>
  )
}

export default App