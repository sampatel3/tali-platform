import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import { AuthProvider } from './context/AuthContext.jsx'
import './index.css'
import { getDocumentTitle } from './config/brand'

document.title = getDocumentTitle();

const darkModeEnabled = typeof window !== 'undefined' && localStorage.getItem('taali_dark_mode') === '1';
if (darkModeEnabled) {
  document.documentElement.classList.add('dark');
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </React.StrictMode>,
)
