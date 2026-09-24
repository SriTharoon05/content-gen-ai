import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import { StoreProvider } from './store.jsx'
import './styles.css'
import { selectedBackend } from './backendChoice'
import CloudflareWorkspace from './pages/CloudflareWorkspace'

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {selectedBackend() === 'cloudflare' ? <CloudflareWorkspace /> : <StoreProvider>
      <App />
    </StoreProvider>}
  </React.StrictMode>,
)
