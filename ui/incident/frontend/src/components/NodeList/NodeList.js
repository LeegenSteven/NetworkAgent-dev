import React, { useState, useEffect, useCallback } from 'react';
import './NodeList.css';
import { getApiUrl, API_ENDPOINTS } from '../../config/apiConfig';
import IncidentDialog from '../IncidentDialog/IncidentDialog';

const NodeList = ({ onSelectNode, selectedNode }) => {
  const [nodes, setNodes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [dialogNode, setDialogNode] = useState(null);

  const fetchNodes = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await fetch(getApiUrl(API_ENDPOINTS.NODES));
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

  const handleDoubleClick = (nodeRelation) => {
    // Check if the node kind is supported
    const supportedKinds = ['UERanSIM', 'WireguardAppliance'];
    const parentKind = nodeRelation.parent.kind;
    
    if (!supportedKinds.includes(parentKind)) {
      alert(`Incident creation for "${parentKind}" nodes is not implemented yet. Currently supported: ${supportedKinds.join(', ')}`);
      return;
    }
    
    setDialogNode(nodeRelation);
    setIsDialogOpen(true);
  };

  const handleCloseDialog = () => {
    setIsDialogOpen(false);
    setDialogNode(null);
  };

  const handleCreateIncident = async (node, incidentType) => {
    console.log('Creating incident:', incidentType, 'on node:', node);
    
    try {
      const response = await fetch(getApiUrl(API_ENDPOINTS.KILL_PROCESS), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          node: node,
          incident_type: incidentType.id
        })
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const result = await response.json();
      
      if (result.success) {
        alert(`Success: ${result.message}`);
      } else {
        alert(`Error: ${result.error || 'Unknown error occurred'}`);
      }
    } catch (error) {
      console.error('Error creating incident:', error);
      alert(`Error creating incident: ${error.message}`);
      throw error; // Re-throw to allow IncidentDialog to handle loading state
    }
  };

  return (
    <section className="node-list-section">
      <div className="node-list-header">
        <div>
          <h2>Select a Node</h2>
          <p className="instruction-text">Double-click a row to create an incident</p>
        </div>
        <button onClick={fetchNodes} className="refresh-button" disabled={loading}>
          {loading ? <i className="fas fa-spinner fa-spin"></i> : <i className="fas fa-sync-alt"></i>}
        </button>
      </div>
      {error && <p className="error-message">{error}</p>}
      <table className="node-table">
        <thead>
          <tr>
            <th>Parent Node</th>
            <th>Parent Type</th>
            <th>Child Node (Compute Instance)</th>
            <th>Child Type</th>
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr>
              <td colSpan="4">Loading nodes...</td>
            </tr>
          ) : (
            nodes.map((nodeRelation, index) => (
              <tr
                key={`${nodeRelation.parent.id}-${nodeRelation.child.id}`}
                onClick={() => onSelectNode(nodeRelation)}
                onDoubleClick={() => handleDoubleClick(nodeRelation)}
                className={`node-row ${selectedNode && selectedNode.parent && selectedNode.parent.id === nodeRelation.parent.id && selectedNode.child.id === nodeRelation.child.id ? 'selected' : ''}`}
              >
                <td>{nodeRelation.parent.name}</td>
                <td>{nodeRelation.parent.kind}</td>
                <td>{nodeRelation.child.name}</td>
                <td>{nodeRelation.child.kind}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
      
      <IncidentDialog
        isOpen={isDialogOpen}
        onClose={handleCloseDialog}
        selectedNode={dialogNode}
        onCreateIncident={handleCreateIncident}
      />
    </section>
  );
};

export default NodeList;
