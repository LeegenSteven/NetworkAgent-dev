import React, { useState } from 'react';
import AgentList from '../AgentList/AgentList';
import NodeList from '../NodeList/NodeList';
import ActionForm from '../ActionForm/ActionForm';
import Header from '../Header/Header';

const DashboardScreen = () => {
  const [selectedAgent, setSelectedAgent] = useState(null);
  const [selectedNode, setSelectedNode] = useState(null);

  const handleSelectAgent = (agent) => {
    setSelectedAgent(agent);
  };

  const handleSelectNode = (node) => {
    setSelectedNode(node);
  };

  return (
    <div className="dashboard-container">
      <Header />
      <main className="dashboard-content">
        <div className="dashboard-grid">
          <div className="grid-column">
            <div className="grid-item">
              <AgentList onSelectAgent={handleSelectAgent} selectedAgent={selectedAgent} />
            </div>
            <div className="grid-item">
              <NodeList onSelectNode={handleSelectNode} selectedNode={selectedNode} />
            </div>
          </div>
          <div className="grid-column">
            <div className="grid-item">
              <ActionForm selectedAgent={selectedAgent} selectedNode={selectedNode} />
            </div>
          </div>
        </div>
      </main>
    </div>
  );
};

export default DashboardScreen;
