import React from 'react';
import { BrowserRouter as Router, Route, Routes } from 'react-router-dom';
import LoginScreen from './components/LoginScreen/LoginScreen';
import DashboardScreen from './components/DashboardScreen/DashboardScreen';
import './App.css';

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<LoginScreen />} />
        <Route path="/dashboard" element={<DashboardScreen />} />
      </Routes>
    </Router>
  );
}

export default App;
