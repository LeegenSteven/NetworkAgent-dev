import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import './LoginScreen.css';
import googleLogo from '../../google.png';
import { getApiUrl, API_ENDPOINTS } from '../../config/apiConfig';

const LoginScreen = () => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [isPasswordVisible, setIsPasswordVisible] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);
  const navigate = useNavigate();

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
          navigate('/dashboard');
        } else {
          setErrorMessage(data.message || 'Invalid username or password');
        }
      } catch (error) {
        setErrorMessage('Login failed. Please try again later.');
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
        <img src={googleLogo} alt="Google Logo" className="logo" />
        <h2>Network Agent Dashboard</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleLogin();
          }}
        >
          <div className="input-group">
            <i className="fas fa-user"></i>
            <input
              type="text"
              placeholder="Username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
            />
          </div>
          <div className="input-group">
            <i className="fas fa-lock"></i>
            <input
              type={isPasswordVisible ? 'text' : 'password'}
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            <i
              className={`fas ${isPasswordVisible ? 'fa-eye-slash' : 'fa-eye'}`}
              onClick={togglePasswordVisibility}
            ></i>
          </div>
          {errorMessage && <p className="error-message">{errorMessage}</p>}
          <button type="submit" disabled={isLoading}>
            {isLoading ? <div className="loader"></div> : 'Login'}
          </button>
        </form>
      </div>
    </div>
  );
};

export default LoginScreen;
