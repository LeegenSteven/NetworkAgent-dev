import React from 'react';
import './HomeScreen.css';

const HomeScreen = ({ user, onNavigate }) => {
  const catalogItems = [
    {
      id: 'slice',
      title: '5G Network Slice',
      description: 'Provision a dedicated network slice with guaranteed QoS, low latency, and high bandwidth tailored for your specific use case.',
      icon: 'fa-network-wired',
      action: () => onNavigate('order')
    },
    {
      id: 'iot',
      title: 'IoT Connectivity',
      description: 'Connect and manage your fleet of IoT devices with secure, scalable, and reliable cellular connectivity solutions.',
      icon: 'fa-microchip',
      action: () => alert('IoT Connectivity ordering coming soon!')
    },
    {
      id: 'edge',
      title: 'Edge Compute',
      description: 'Deploy low-latency applications closer to your users with our distributed Mobile Edge Computing infrastructure.',
      icon: 'fa-server',
      action: () => alert('Edge Compute ordering coming soon!')
    }
  ];

  return (
    <div className="home-container">
      <header className="home-header">
        <h1>Welcome back, {user ? user.username : 'Guest'}</h1>
        <p>What would you like to order today?</p>
      </header>

      <div className="catalog-grid">
        {catalogItems.map((item) => (
          <div key={item.id} className="catalog-card" onClick={item.action}>
            <div className="card-icon">
              <i className={`fas ${item.icon}`}></i>
            </div>
            <h3>{item.title}</h3>
            <p>{item.description}</p>
            <div className="card-action">
              Order Now <i className="fas fa-arrow-right"></i>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default HomeScreen;
