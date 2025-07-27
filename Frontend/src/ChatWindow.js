import React from 'react';
import './styles.css';

function ChatWindow({ data }) {
  const { best_model, best_solution, solutions } = data;

  return (
    <div className="chat-window">
      <div className="chat-header">
        <h3>Results</h3>
        <p><strong>Best Model:</strong> {best_model}</p>
      </div>

      <div className="solution-best">
  <h4>Best Solution</h4>
  <p><strong>Model:</strong> {best_solution.model}</p>
  <p><strong>Time Complexity:</strong> {best_solution.time_complexity}</p>
  <p><strong>Space Complexity:</strong> {best_solution.space_complexity}</p>
  <p><strong>Feedback:</strong> {best_solution.test_feedback}</p>
  <pre className="code-block">{best_solution.code}</pre>
</div>


      <div className="solution-list">
        <h4>All Model Solutions</h4>
        {solutions.map((s, index) => (
          <div key={index} className="solution-item">
            <p><strong>Model:</strong> {s.model}</p>
            <p><strong>Time:</strong> {s.time_complexity}</p>
            <p><strong>Space:</strong> {s.space_complexity}</p>
            <p><strong>Feedback:</strong> {s.test_feedback}</p>
            <pre className="code-block">{s.code}</pre>
          </div>
        ))}
      </div>
    </div>
  );
}

export default ChatWindow;
