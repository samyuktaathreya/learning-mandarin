import { useState } from 'react';
import { useNavigate } from 'react-router-dom'; // 1. Import the hook

function Header() {
  const [count, setCount] = useState(0);
  const navigate = useNavigate(); // 2. Initialize the navigate function

  return (
    <div className="header-container">
        <button onClick={() => navigate("/")}>
            Home
        </button>

        <button onClick={() => navigate("/interactive-textbook")}>
            Interactive Textbook
        </button>

        <button onClick={() => navigate("/listening-practice")}>
            Listening Practice
        </button>

        <button onClick={() => navigate("/duolingo-style-practice")}>
            Duolingo Style Questions
        </button>
    </div>
  );
}

export default Header;