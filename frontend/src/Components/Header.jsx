import { useNavigate } from 'react-router-dom';

function Header() {
  const navigate = useNavigate();

  return (
    <div className="header-container">
        <button onClick={() => navigate("/")}>Home</button>
    </div>
  );
}

export default Header;