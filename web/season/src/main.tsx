import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { HashRouter } from 'react-router-dom';
import { CacheProvider } from '@emotion/react';
import createCache from '@emotion/cache';
import { createTheme, CssBaseline, ThemeProvider } from '@mui/material';
import { App } from './App.tsx';
import { ApiClient } from './api.ts';
import { OperationsContext } from './hooks.tsx';
import { Operations } from './operations.ts';

const nonce = document.querySelector<HTMLMetaElement>('meta[name="csp-nonce"]')?.content;
const cache = createCache({ key: 'ffopt', nonce });
const theme = createTheme({
  palette: {
    mode: 'light',
    primary: { main: '#245a81' },
    background: { default: '#f8fafb', paper: '#ffffff' },
    text: { primary: '#172b3a', secondary: '#526370' },
  },
  typography: {
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    h1: { fontSize: '1.65rem', fontWeight: 700, lineHeight: 1.3 },
    h2: { fontSize: '1.125rem', fontWeight: 650, lineHeight: 1.4 },
    h3: { fontSize: '1rem', fontWeight: 650, lineHeight: 1.4 },
    body2: { lineHeight: 1.55 },
    button: { textTransform: 'none', fontWeight: 600 },
  },
  shape: { borderRadius: 8 },
  components: {
    MuiButton: { defaultProps: { disableElevation: true }, styleOverrides: { root: { minHeight: 44 } } },
    MuiMenuItem: { styleOverrides: { root: { minHeight: 44, whiteSpace: 'normal' } } },
    MuiAccordion: { styleOverrides: { root: { boxShadow: 'none', '&::before': { display: 'none' } } } },
    MuiAccordionSummary: { styleOverrides: { root: { minHeight: 52 } } },
    MuiTableCell: { styleOverrides: { root: { padding: '10px 14px', overflowWrap: 'anywhere' } } },
    MuiTypography: { styleOverrides: { root: { overflowWrap: 'anywhere' } } },
    MuiCssBaseline: {
      styleOverrides: {
        body: { margin: 0 },
        '#root': { minHeight: '100vh' },
        'caption': { captionSide: 'bottom', padding: '12px 14px', textAlign: 'left', fontSize: '0.75rem' },
        ':focus-visible': { outline: '3px solid #245a81', outlineOffset: 3 },
        '@media (prefers-reduced-motion: reduce)': {
          '*, *::before, *::after': { animationDuration: '0.01ms !important', transitionDuration: '0.01ms !important', scrollBehavior: 'auto !important' },
        },
      },
    },
  },
});
let storage: Storage | undefined;
try { storage = window.sessionStorage; } catch { /* Operations warns when storage is unavailable; in-memory recovery still works. */ }
const operations = new Operations(new ApiClient(location.origin), storage);
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <CacheProvider value={cache}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        <OperationsContext.Provider value={operations}>
          <HashRouter><App /></HashRouter>
        </OperationsContext.Provider>
      </ThemeProvider>
    </CacheProvider>
  </StrictMode>,
);
