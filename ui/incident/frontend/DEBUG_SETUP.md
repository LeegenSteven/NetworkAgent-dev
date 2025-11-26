# Debug Configuration for Incident Agent Frontend

This document explains how to configure the React frontend to call backend APIs from `http://127.0.0.1:8080` when in debug mode.

## Environment Variable Configuration

The frontend uses the `REACT_APP_DEBUG` environment variable to determine which API endpoint to use:

- **Debug Mode** (`REACT_APP_DEBUG=true`): API calls go to `http://127.0.0.1:8080`
- **Production Mode** (`REACT_APP_DEBUG=false` or unset): API calls use relative paths (same origin)

## Setup Methods

### Method 1: Using .env file (Recommended for development)

1. Create or edit the `.env` file in the frontend directory:
   ```bash
   cd networkagents/incident/frontend
   echo "REACT_APP_DEBUG=true" > .env
   ```

2. Start the React development server:
   ```bash
   npm start
   ```

### Method 2: Using environment variable directly

```bash
cd networkagents/incident/frontend
REACT_APP_DEBUG=true npm start
```

### Method 3: Using cross-env (if installed)

```bash
cd networkagents/incident/frontend
npx cross-env REACT_APP_DEBUG=true npm start
```

## API Endpoints

When `REACT_APP_DEBUG=true`, the following API calls will be made to `http://127.0.0.1:8080`:

- `/login` → `http://127.0.0.1:8080/login`
- `/api/agents` → `http://127.0.0.1:8080/api/agents`
- `/api/nodes` → `http://127.0.0.1:8080/api/nodes`
- `/api/start_task` → `http://127.0.0.1:8080/api/start_task`
- `/api/task/{id}` → `http://127.0.0.1:8080/api/task/{id}`

## Production Build

For production builds, ensure `REACT_APP_DEBUG` is not set or is set to `false`:

```bash
cd networkagents/incident/frontend
REACT_APP_DEBUG=false npm run build
```

## Troubleshooting

1. **CORS Issues**: Make sure your backend server at `127.0.0.1:8080` has CORS configured to allow requests from your React development server (typically `http://localhost:3000`).

2. **Environment Variable Not Working**: 
   - Ensure the variable starts with `REACT_APP_`
   - Restart the development server after changing environment variables
   - Check that the `.env` file is in the correct location (frontend root directory)

3. **API Calls Still Going to Wrong Endpoint**:
   - Check browser developer tools Network tab to verify the actual URLs being called
   - Ensure all components are importing and using the `getApiUrl` function from `config/apiConfig.js`

## Implementation Details

The configuration is handled in `src/config/apiConfig.js`:

- `getApiBaseUrl()` function checks the environment variable
- `getApiUrl(endpoint)` helper function constructs full URLs
- All React components import and use these utilities for API calls
