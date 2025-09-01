import React, { useState } from 'react';
import './IncidentDialog.css';

const IncidentDialog = ({ isOpen, onClose, selectedNode, onCreateIncident }) => {
  const [loadingIncident, setLoadingIncident] = useState(null);
  const incidentTypes = [
    {
      id: 'kill-process',
      name: 'Kill Process',
      description: 'Terminate a specific process on the node',
      icon: '🔴'
    },
    {
      id: 'throttle-interface',
      name: 'Throttle Interface',
      description: 'Reduce network interface bandwidth',
      icon: '🐌'
    },
  ];

  const handleIncidentClick = async (incidentType) => {
    setLoadingIncident(incidentType.id);
    try {
      await onCreateIncident(selectedNode, incidentType);
      onClose();
    } catch (error) {
      console.error('Error creating incident:', error);
    } finally {
      setLoadingIncident(null);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="incident-dialog-overlay" onClick={onClose}>
      <div className="incident-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="incident-dialog-header">
          <h3>Create Incident</h3>
          <button className="close-button" onClick={onClose}>×</button>
        </div>
        
        <div className="incident-dialog-content">
          <div className="selected-node-info">
            <h4>Target Node:</h4>
            {selectedNode && (
              <div className="node-details">
                <p><strong>Parent:</strong> {selectedNode.parent.name} ({selectedNode.parent.kind})</p>
                <p><strong>Child:</strong> {selectedNode.child.name} ({selectedNode.child.kind})</p>
              </div>
            )}
          </div>
          
          <div className="incident-types">
            <h4>Select Incident Type:</h4>
            <div className="incident-grid">
              {incidentTypes.map((incident) => (
                <div
                  key={incident.id}
                  className={`incident-card ${loadingIncident === incident.id ? 'loading' : ''}`}
                  onClick={() => loadingIncident === null ? handleIncidentClick(incident) : null}
                >
                  <div className="incident-content">
                    <div className="incident-icon">{incident.icon}</div>
                    <div className="incident-info">
                      <h5>{incident.name}</h5>
                      <p>{incident.description}</p>
                    </div>
                  </div>
                  {loadingIncident === incident.id && (
                    <div className="loading-overlay">
                      <div className="spinner"></div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default IncidentDialog;
