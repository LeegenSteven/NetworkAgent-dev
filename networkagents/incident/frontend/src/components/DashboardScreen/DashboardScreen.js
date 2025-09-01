import React, { useState } from 'react';
import NodeList from '../NodeList/NodeList';
import Header from '../Header/Header';

const DashboardScreen = () => {
  const [selectedNode, setSelectedNode] = useState(null);

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
              <NodeList onSelectNode={handleSelectNode} selectedNode={selectedNode} />
            </div>
          </div>
        </div>
      </main>
    </div>
  );
};

export default DashboardScreen;
