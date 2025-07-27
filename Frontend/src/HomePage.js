import React, { useState } from 'react';
import axios from 'axios';
import ChatWindow from './ChatWindow';
import { useNavigate } from 'react-router-dom';
import './styles.css';

function HomePage() {
  const [problemText, setProblemText] = useState('');
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleLogout = () => {
    navigate('/');
  };

  const handleSubmit = async () => {
    setLoading(true);
    try {
      const response = await axios.post('http://localhost:5000/solve', {
        problem_id: 'custom_problem_1',
        description: problemText,
        function_name: 'two_sum'
      });
      setResults(response.data);
    } catch (error) {
      console.error('Error fetching solution:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="container">
      <div className="top-right">
        <button className="logout-btn-small" onClick={handleLogout}>Logout</button>
      </div>
      <h2 className="mac-logo">MAC</h2>

      <textarea
        rows={8}
        value={problemText}
        onChange={(e) => setProblemText(e.target.value)}
        placeholder="Enter your LeetCode-style problem here..."
      />

      <div className="button-wrapper">
        <button onClick={handleSubmit} disabled={loading}>
          {loading ? 'Solving...' : 'Submit'}
        </button>
      </div>

      {results && <ChatWindow data={results} />}
    </div>
  );
}

export default HomePage;
