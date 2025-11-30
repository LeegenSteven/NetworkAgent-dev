import React, { useState, useEffect } from 'react';
import OrderSlice from './components/OrderSlice/OrderSlice';
import MySlices from './components/MySlices/MySlices';
import LoginScreen from './components/LoginScreen/LoginScreen';
import HomeScreen from './components/HomeScreen/HomeScreen';
import io from 'socket.io-client';
import { SOCKET_BASE_URL } from './config/apiConfig';
import { getTheme } from './config/themeConfig';
import './App.css';

const socket = io(SOCKET_BASE_URL);

function App() {
  const [activeTab, setActiveTab] = useState('home');
  const [slices, setSlices] = useState([]);
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [user, setUser] = useState(null);
  const theme = getTheme();

  useEffect(() => {
    // Apply theme colors to CSS variables
    const root = document.documentElement;
    root.style.setProperty('--primary-color', theme.colors.primary);
    root.style.setProperty('--primary-hover', theme.colors.primaryHover);
    root.style.setProperty('--background-color', theme.colors.background);
    root.style.setProperty('--text-color', theme.colors.text);
    root.style.setProperty('--text-light', theme.colors.textLight);
    root.style.setProperty('--white', theme.colors.white);
    root.style.setProperty('--light-overlay', theme.colors.lightOverlay);
    root.style.setProperty('--primary-light', theme.colors.primaryLight);
    root.style.setProperty('--border-color', theme.colors.border);
    root.style.setProperty('--header-background', theme.colors.headerBackground);

    // Listen for 'connect' event
    socket.on('connect', () => {
      console.log('Connected to the server');
    });

    // Listen for 'disconnect' event
    socket.on('disconnect', () => {
      console.log('Disconnected from the server');
    });

    // Clean up the socket connection when the component unmounts
    return () => {
      socket.disconnect();
    };
  }, [theme]);

  useEffect(() => {
    if (process.env.REACT_APP_DEBUG === 'true') {
      handleLogin({ username: 'admin' });
    }
  }, []);

  const handleLogin = (userData) => {
    setUser(userData);
    setIsLoggedIn(true);
    setActiveTab('home');
  };

  if (!isLoggedIn) {
    return <LoginScreen onLogin={handleLogin} theme={theme} />;
  }

  return (
    <div className="App">
      <header className="header">
        <div className="header-content">
          <div className="logo-section" onClick={() => setActiveTab('home')} style={{ cursor: 'pointer' }}>
            <img src={theme.assets.logo} alt={theme.text.title} className="telekom-logo" />
            <span className="portal-title">{theme.text.title}</span>
          </div>
          <nav className="nav-tabs">
            <button
              className={`nav-tab ${activeTab === 'home' ? 'active' : ''}`}
              onClick={() => setActiveTab('home')}
            >
              Home
            </button>
            <button
              className={`nav-tab ${activeTab === 'order' ? 'active' : ''}`}
              onClick={() => setActiveTab('order')}
            >
              Order Slice
            </button>
            <button
              className={`nav-tab ${activeTab === 'slices' ? 'active' : ''}`}
              onClick={() => setActiveTab('slices')}
            >
              My Slices
            </button>
            <button
              className={`nav-tab ${activeTab === 'analytics' ? 'active' : ''}`}
              onClick={() => setActiveTab('analytics')}
            >
              Analytics
            </button>
          </nav>
        </div>
      </header>

      <main className="main-content">
        {activeTab === 'home' && <HomeScreen user={user} onNavigate={setActiveTab} theme={theme} />}
        {activeTab === 'order' && <OrderSlice socket={socket} user={user} onNavigate={setActiveTab} theme={theme} />}
        {activeTab === 'slices' && <MySlices socket={socket} slices={slices} user={user} theme={theme} />}
        {activeTab === 'analytics' && <div>Analytics - Coming Soon</div>}
      </main>
    </div>
  );
}

export default App;
