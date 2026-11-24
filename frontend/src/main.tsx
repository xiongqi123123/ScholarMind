import '@fontsource-variable/source-serif-4/opsz.css'
import '@fontsource-variable/source-sans-3/wght.css'
import 'react-day-picker/style.css'
import './index.css'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
