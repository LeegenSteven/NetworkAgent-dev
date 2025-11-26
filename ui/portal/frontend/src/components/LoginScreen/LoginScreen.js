import React, { useState } from 'react';
import './LoginScreen.css';
import { getApiUrl, API_ENDPOINTS } from '../../config/apiConfig';

const LoginScreen = ({ onLogin, theme }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [isPasswordVisible, setIsPasswordVisible] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);

  const handleLogin = async () => {
    if (username && password) {
      setIsLoading(true);
      setErrorMessage(null);
      try {
        const response = await fetch(getApiUrl(API_ENDPOINTS.LOGIN), {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ username, password }),
        });
        const data = await response.json();
        if (data.success) {
          onLogin(data.user || { username });
        } else {
          setErrorMessage(data.message || 'Invalid username or password');
        }
      } catch (error) {
        setErrorMessage('Login failed. Please try again later.');
        console.error("Login error:", error);
      } finally {
        setIsLoading(false);
      }
    }
  };

  const togglePasswordVisibility = () => {
    setIsPasswordVisible(!isPasswordVisible);
  };

  return (
    <div className="login-container">
      <div className="login-card">
        <div className="logo-section">
          <img src={theme ? theme.assets.logo : "/dt-icon.png"} alt="Logo" className="portal-logo" />
          <h1 className="login-title">{theme ? theme.text.loginTitle : "5G Slice Portal"}</h1>
        </div>
        <h2>Sign In</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleLogin();
          }}
        >
          <div className="input-group">
            <label className="input-label">Username</label>
            <div className="input-wrapper">
              <i className="fas fa-user"></i>
              <input
                type="text"
                placeholder="Enter your username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </div>
          </div>
          <div className="input-group">
            <label className="input-label">Password</label>
            <div className="input-wrapper">
              <i className="fas fa-lock"></i>
              <input
                type={isPasswordVisible ? 'text' : 'password'}
                placeholder="Enter your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
              <i
                className={`fas ${isPasswordVisible ? 'fa-eye-slash' : 'fa-eye'}`}
                onClick={togglePasswordVisibility}
              ></i>
            </div>
          </div>
          {errorMessage && <p className="error-message">{errorMessage}</p>}
          <button type="submit" className="login-button" disabled={isLoading}>
            {isLoading ? <div className="loader"></div> : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  );
};

export default LoginScreen;
