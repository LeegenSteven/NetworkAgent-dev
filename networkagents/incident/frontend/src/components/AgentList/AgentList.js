import React, { useState, useEffect, useCallback } from 'react';
import './AgentList.css';

const AgentList = ({ onSelectAgent, selectedAgent }) => {
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchAgents = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/agents');
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      setAgents(data);
    } catch (e) {
      setError(e.message);
      console.error('Error fetching agents:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  return (
    <section className="agent-list-section">
      <div className="agent-list-header">
        <h2>Agents</h2>
        <button onClick={fetchAgents} className="refresh-button" disabled={loading}>
          {loading ? <i className="fas fa-spinner fa-spin"></i> : <i className="fas fa-sync-alt"></i>}
        </button>
      </div>
      {error && <p className="error-message">{error}</p>}
      <table className="agent-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>URL</th>
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr>
              <td colSpan="2">Loading agents...</td>
            </tr>
          ) : (
            agents.map((agent, index) => (
              <tr
                key={index}
                onClick={() => onSelectAgent(agent)}
                className={`agent-row ${selectedAgent && selectedAgent.name === agent.name ? 'selected' : ''}`}
              >
                <td>{agent.name}</td>
                <td>{agent.url}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </section>
  );
};

export default AgentList;
