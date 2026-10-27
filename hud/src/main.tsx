import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { demoRespond } from './demoResponder'
import { createJarvisResponder, probeJarvisServer } from './jarvisResponder'
import './styles.css'

// If the Jarvis Python server (./start.sh) is running, replies come from Claude through it.
// Otherwise the clearly labelled demo handler answers. Add ?demo to the URL to force demo mode.
async function start() {
  const root = createRoot(document.getElementById('root')!)
  const forceDemo = new URLSearchParams(location.search).has('demo')
  const server = forceDemo ? null : await probeJarvisServer()

  root.render(
    <StrictMode>
      {server ? (
        <App respond={createJarvisResponder()} engine="claude" model={server.model} />
      ) : (
        <App respond={demoRespond} engine="demo" />
      )}
    </StrictMode>,
  )
}

void start()
