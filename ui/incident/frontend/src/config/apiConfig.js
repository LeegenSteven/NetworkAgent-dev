// API Configuration
// Uses localhost:8080 when DEBUG environment variable is set, otherwise uses relative paths

const getApiBaseUrl = () => {
  // Check if DEBUG environment variable is set
  const isDebug = process.env.REACT_APP_DEBUG === 'true' || process.env.NODE_ENV === 'development';
  
  if (isDebug) {
    return 'http://127.0.0.1:8080';
  }
  
  // Use relative paths for production (served from same origin)
  return '';
};

export const API_BASE_URL = getApiBaseUrl();

// Helper function to construct full API URLs
export const getApiUrl = (endpoint) => {
  // Remove leading slash if present to avoid double slashes
  const cleanEndpoint = endpoint.startsWith('/') ? endpoint.slice(1) : endpoint;
  return `${API_BASE_URL}/${cleanEndpoint}`;
};

// API endpoints
export const API_ENDPOINTS = {
  LOGIN: '/login',
  AGENTS: '/api/agents',
  NODES: '/api/nodes',
  START_TASK: '/api/start_task',
  TASK_STATUS: '/api/task',
  KILL_PROCESS: '/api/killprocess'
};
