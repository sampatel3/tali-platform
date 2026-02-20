import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import { AuthProvider } from './context/AuthContext.jsx'
import './index.css'
import { getDocumentTitle } from './config/brand'
import { applyDarkModeClass, readDarkModePreference } from './lib/themePreference'

document.title = getDocumentTitle();

applyDarkModeClass(readDarkModePreference());

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </React.StrictMode>,
)
