import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { demoRespond } from './demoResponder'
import './styles.css'

// Responses come from the local DEMO handler; the Claude API is not connected.
// When a real Claude responder exists, pass it here and set engine="claude".
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App respond={demoRespond} engine="demo" />
  </StrictMode>,
)
