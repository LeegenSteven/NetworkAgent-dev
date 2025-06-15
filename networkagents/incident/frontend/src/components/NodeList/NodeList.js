import React, { useState, useEffect, useCallback } from 'react';
import './NodeList.css';

const NodeList = ({ onSelectNode, selectedNode }) => {
  const [nodes, setNodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchNodes = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch('/api/nodes');
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      setNodes(data);
    } catch (e) {
      setError(e.message);
      console.error('Error fetching nodes:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchNodes();
  }, [fetchNodes]);

  return (
    <section className="node-list-section">
      <div className="node-list-header">
        <h2>Select a Node</h2>
        <button onClick={fetchNodes} className="refresh-button" disabled={loading}>
          {loading ? <i className="fas fa-spinner fa-spin"></i> : <i className="fas fa-sync-alt"></i>}
        </button>
      </div>
      {error && <p className="error-message">{error}</p>}
      <table className="node-table">
        <thead>
          <tr>
            <th>Name</th>
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr>
              <td colSpan="1">Loading nodes...</td>
            </tr>
          ) : (
            nodes.map((node) => (
              <tr
                key={node.id}
                onClick={() => onSelectNode(node)}
                className={`node-row ${selectedNode && selectedNode.id === node.id ? 'selected' : ''}`}
              >
                <td>{node.name}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </section>
  );
};

export default NodeList;
