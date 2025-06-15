import React, { useState, useEffect } from 'react';
import './ActionForm.css';

const ActionForm = ({ selectedAgent, selectedNode }) => {
  const [agentName, setAgentName] = useState('');
  const [agentUrl, setAgentUrl] = useState('');
  const [nodeName, setNodeName] = useState('');
  const [objective, setObjective] = useState('create a new UPF called brian-upf and attach to two network locations, ingress = brian-ingress and egress = brian-egress');
  const [tableData, setTableData] = useState([
    { key: '', value: '' },
  ]);
  const [selectedRow, setSelectedRow] = useState(null);
  const [taskStatus, setTaskStatus] = useState('');
  const [isTaskRunning, setIsTaskRunning] = useState(false);

  useEffect(() => {
    if (selectedAgent) {
      setAgentName(selectedAgent.name);
      setAgentUrl(selectedAgent.url);
    }
    if (selectedNode) {
      setNodeName(selectedNode.name);
    } 
  }, [selectedAgent, selectedNode]);

  const handleAddRow = () => {
    setTableData([...tableData, { key: '', value: '' }]);
  };

  const handleRemoveRow = () => {
    if (selectedRow !== null) {
      const newTableData = [...tableData];
      newTableData.splice(selectedRow, 1);
      setTableData(newTableData);
      setSelectedRow(null);
    }
  };

  const handleTableDataChange = (index, field, value) => {
    const newTableData = [...tableData];
    newTableData[index][field] = value;
    setTableData(newTableData);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!agentName || !nodeName) {
      alert('Please select an agent and a node.');
      return;
    }
    setIsTaskRunning(true);
    setTaskStatus('Running');
    try {
      const response = await fetch('/api/start_task', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          agentName,
          agentUrl,
          nodeName,
          objective,
          tableData,
        }),
      });
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const data = await response.json();
      console.log('Task started:', data);
      setTaskStatus(`Running (Task ID: ${data.task_id})`);

      const pollInterval = setInterval(async () => {
        try {
          const statusResponse = await fetch(`${window.location.origin}/api/task/${data.task_id}?agentUrl=${encodeURIComponent(agentUrl)}`);
          if (!statusResponse.ok) {
            throw new Error(`HTTP error! status: ${statusResponse.status}`);
          }
          const statusData = await statusResponse.json();
          setTaskStatus(`Running (Task ID: ${data.task_id}, Status: ${statusData.status})`);
          if (statusData.status === 'completed') {
            clearInterval(pollInterval);
            setIsTaskRunning(false);
            setTaskStatus('Completed');
          }
        } catch (error) {
          console.error('Error polling task status:', error);
          clearInterval(pollInterval);
          setIsTaskRunning(false);
          setTaskStatus('Error polling status');
        }
      }, 5000);
    } catch (error) {
      console.error('Error starting task:', error);
      setTaskStatus('Error');
      setIsTaskRunning(false);
    }
  };

  return (
    <div className="action-form-container">
      <h2>Send Agent Task</h2>
      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label>Agent Name</label>
          <input
            type="text"
            value={agentName}
            onChange={(e) => setAgentName(e.target.value)}
          />
        </div>
        <div className="form-group">
          <label>Node Name</label>
          <input
            type="text"
            value={nodeName}
            onChange={(e) => setNodeName(e.target.value)}
          />
        </div>
        <div className="form-group">
          <label>Objective</label>
          <textarea
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
          />
        </div>
        <div className="form-group-column">
          <label>Additional Information</label>
          <div className="table-toolbar">
            <button type="button" onClick={handleAddRow} className="add-row-button">
              <i className="fas fa-plus"></i>
            </button>
            <button type="button" onClick={handleRemoveRow} className="remove-row-button" disabled={selectedRow === null}>
              <i className="fas fa-minus"></i>
            </button>
          </div>
          <table className="info-table">
            <thead>
              <tr>
                <th>Data</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              {tableData.map((row, index) => (
                <tr key={index} onClick={() => setSelectedRow(index)} className={selectedRow === index ? 'selected' : ''}>
                  <td>
                    <input
                      type="text"
                      value={row.key}
                      onChange={(e) =>
                        handleTableDataChange(index, 'key', e.target.value)
                      }
                    />
                  </td>
                  <td>
                    <input
                      type="text"
                      value={row.value}
                      onChange={(e) =>
                        handleTableDataChange(index, 'value', e.target.value)
                      }
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <button type="submit" className="start-task-button" disabled={isTaskRunning || !agentName || !nodeName}>Start Task</button>
      </form>
      {taskStatus && (
        <div className="task-status-section">
          <h3>Task Status</h3>
          <p>Status: {taskStatus}</p>
        </div>
      )}
    </div>
  );
};

export default ActionForm;
