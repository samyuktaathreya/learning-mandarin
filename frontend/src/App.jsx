import { BrowserRouter, Routes, Route } from "react-router-dom"
import DuolingoStyleQuestions from "./pages/DuolingoStylePractice"

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/duolingo-style-practice" element={<DuolingoStyleQuestions />} />
        <Route path="*" element={<div>I am lost! Current path: {window.location.pathname}</div>} />
      </Routes>
    </BrowserRouter>
  )
}

export default App