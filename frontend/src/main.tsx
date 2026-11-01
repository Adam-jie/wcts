import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { setNonce } from 'get-nonce';
import App from './App';
import '../../web/theme.css';
import '@xyflow/react/dist/style.css';
import './styles.css';

const nonce = document.querySelector<HTMLMetaElement>('meta[name="style-nonce"]')?.content;
if (nonce && nonce !== '__TESTFIX_STYLE_NONCE__') setNonce(nonce);
createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
