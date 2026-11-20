import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { httpApi } from './api';
import { App } from './App';
import { Services } from './context';
import './tokens.css';
import './app.css';

const root = document.getElementById('root');
if (!root) throw new Error('index.html has no element with the id "root".');

createRoot(root).render(
  <StrictMode>
    <Services api={httpApi}>
      <App />
    </Services>
  </StrictMode>,
);
