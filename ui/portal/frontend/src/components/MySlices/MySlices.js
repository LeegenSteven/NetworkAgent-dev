import React from 'react';
import './MySlices.css';

const MySlices = () => {
  return (
    <div className="my-slices-container">
      <div className="status-section">
        <h2 className="status-title">Status: <span className="status-active">Active</span></h2>
        
        <div className="map-widget">
          <h3 className="map-title">5G Slice Coverage Area</h3>
          <div className="map-container">
            <div className="map-view">
              <div className="coverage-overlay">
                <div className="coverage-zone active-zone">
                  <div className="zone-marker primary">
                    <span className="marker-icon">📍</span>
                    <span className="marker-label">Leverkusen</span>
                  </div>
                </div>
                <div className="coverage-zone secondary-zone">
                  <div className="zone-marker secondary">
                    <span className="marker-icon">📶</span>
                    <span className="marker-label">Coverage Area</span>
                  </div>
                </div>
              </div>
              <div className="map-controls">
                <button className="map-control-btn">+</button>
                <button className="map-control-btn">-</button>
              </div>
            </div>
            <div className="map-legend">
              <div className="legend-item">
                <div className="legend-color active"></div>
                <span>Active Coverage</span>
              </div>
              <div className="legend-item">
                <div className="legend-color potential"></div>
                <span>Potential Coverage</span>
              </div>
            </div>
          </div>
        </div>

        <div className="metrics-row">
          <div className="metric-card">
            <div className="circular-progress bandwidth">
              <svg className="progress-ring" width="120" height="120">
                <circle
                  className="progress-ring__circle-bg"
                  stroke="#f0f0f0"
                  strokeWidth="8"
                  fill="transparent"
                  r="52"
                  cx="60"
                  cy="60"
                />
                <circle
                  className="progress-ring__circle"
                  stroke="#e20074"
                  strokeWidth="8"
                  fill="transparent"
                  r="52"
                  cx="60"
                  cy="60"
                  strokeDasharray={`${(248/250) * 326.7} 326.7`}
                />
              </svg>
              <div className="progress-content">
                <div className="progress-value">248</div>
                <div className="progress-total">248 / 250</div>
              </div>
            </div>
            <p className="metric-label">Bandwidth (Mbps)</p>
          </div>

          <div className="metric-card">
            <div className="circular-progress latency">
              <svg className="progress-ring" width="120" height="120">
                <circle
                  className="progress-ring__circle-bg"
                  stroke="#f0f0f0"
                  strokeWidth="8"
                  fill="transparent"
                  r="52"
                  cx="60"
                  cy="60"
                />
                <circle
                  className="progress-ring__circle"
                  stroke="#007bff"
                  strokeWidth="8"
                  fill="transparent"
                  r="52"
                  cx="60"
                  cy="60"
                  strokeDasharray={`${(9/20) * 326.7} 326.7`}
                />
              </svg>
              <div className="progress-content">
                <div className="progress-value">9</div>
                <div className="progress-label-small">Latency (ms)</div>
              </div>
            </div>
            <p className="metric-label">Latency (ms)</p>
          </div>

          <div className="metric-card">
            <div className="circular-progress uptime">
              <svg className="progress-ring" width="120" height="120">
                <circle
                  className="progress-ring__circle-bg"
                  stroke="#f0f0f0"
                  strokeWidth="8"
                  fill="transparent"
                  r="52"
                  cx="60"
                  cy="60"
                />
                <circle
                  className="progress-ring__circle"
                  stroke="#28a745"
                  strokeWidth="8"
                  fill="transparent"
                  r="52"
                  cx="60"
                  cy="60"
                  strokeDasharray={`${(23.5/24) * 326.7} 326.7`}
                />
              </svg>
              <div className="progress-content">
                <div className="progress-value">23.5</div>
                <div className="progress-label-small">/24 hours</div>
              </div>
            </div>
            <p className="metric-label">Uptime (hours)</p>
          </div>
        </div>
      </div>

      <div className="charts-section">
        <div className="chart-card">
          <h3>Data Transfer (GB) - Last 24h</h3>
          <div className="chart-placeholder">
            <svg className="chart-svg" width="100%" height="150">
              <polyline
                fill="none"
                stroke="#e20074"
                strokeWidth="3"
                points="20,130 60,100 100,80 140,60 180,40 220,30 260,20"
              />
              <circle cx="20" cy="130" r="3" fill="#e20074" />
              <circle cx="60" cy="100" r="3" fill="#e20074" />
              <circle cx="100" cy="80" r="3" fill="#e20074" />
              <circle cx="140" cy="60" r="3" fill="#e20074" />
              <circle cx="180" cy="40" r="3" fill="#e20074" />
              <circle cx="220" cy="30" r="3" fill="#e20074" />
              <circle cx="260" cy="20" r="3" fill="#e20074" />
            </svg>
          </div>
        </div>

        <div className="chart-card">
          <h3>Packet Loss (%)</h3>
          <div className="chart-placeholder">
            <svg className="chart-svg" width="100%" height="150">
              <polyline
                fill="none"
                stroke="#007bff"
                strokeWidth="3"
                points="20,120 60,110 100,115 140,105 180,100 220,95 260,85"
              />
              <circle cx="20" cy="120" r="3" fill="#007bff" />
              <circle cx="60" cy="110" r="3" fill="#007bff" />
              <circle cx="100" cy="115" r="3" fill="#007bff" />
              <circle cx="140" cy="105" r="3" fill="#007bff" />
              <circle cx="180" cy="100" r="3" fill="#007bff" />
              <circle cx="220" cy="95" r="3" fill="#007bff" />
              <circle cx="260" cy="85" r="3" fill="#007bff" />
            </svg>
          </div>
        </div>
      </div>

      <div className="slice-info">
        <div className="slice-details">
          <p><strong>Slice ID:</strong> 5G-SLICE-20241026-001</p>
          <p><strong>Active Since:</strong> 2024-26 10:00:00</p>
        </div>
        <button className="manage-slice-btn">Manage Slice</button>
      </div>
    </div>
  );
};

export default MySlices;
