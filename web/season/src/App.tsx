import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import {
  Alert, AppBar, Box, Button, Container, LinearProgress, Link, Menu, MenuItem,
  Stack, Toolbar, Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { Link as RouterLink, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { AuthDialog, Connection } from './Connection.tsx';
import { History, HistoricalAdvice } from './History.tsx';
import { Loading } from './components.tsx';
import { Week } from './Week.tsx';
import { RouteErrorBoundary } from './RouteErrorBoundary.tsx';
import { useOperations } from './hooks.tsx';
import { activeJob } from './operations.ts';

const Research = lazy(() => import('./Research.tsx').then(module => ({ default: module.Research })));
const DataService = lazy(() => import('./DataService.tsx').then(module => ({ default: module.DataService })));

export function App() {
  const { operations, state } = useOperations();
  const location = useLocation();
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const main = useRef<HTMLElement | null>(null);
  const active = state.jobs.some(activeJob) || Boolean(state.tracking);
  const home = location.pathname === '/';
  const needsRetry = Boolean(state.pending && !state.submitting);
  const moreItems = [
    { to: '/research', label: 'Research lab' },
    { to: '/data', label: 'Data and service' },
    { to: '/connection', label: 'Connection' },
  ];

  useEffect(() => { void operations.connect(); }, [operations]);
  useEffect(() => {
    document.title = `${home ? 'My week' : location.pathname.startsWith('/past') ? 'Past advice'
      : location.pathname === '/research' ? 'Research lab' : location.pathname === '/data' ? 'Data and service' : 'Connection'} · Fantasy assistant`;
    main.current?.focus();
  }, [location.pathname, home]);
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (operations.getSnapshot().pending) event.preventDefault();
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [operations]);
  useEffect(() => {
    if (!state.status || state.authRequired) return;
    let timer: ReturnType<typeof setTimeout>;
    let cancelled = false;
    const poll = async () => {
      await operations.poll();
      if (!cancelled) timer = setTimeout(poll, active ? 2000 : 15_000);
    };
    timer = setTimeout(poll, active ? 1000 : 15_000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [operations, active, state.status, state.authRequired]);

  return <>
    <Link href="#main-content" onClick={event => { event.preventDefault(); main.current?.focus(); }}
      sx={{ position: 'absolute', left: 8, top: -100, zIndex: 1500, bgcolor: 'background.paper', p: 1.5,
        '&:focus': { top: 8 } }}>Skip to content</Link>
    <AppBar position="static" color="default" elevation={0} sx={{ bgcolor: 'background.paper', borderBottom: 1, borderColor: 'divider' }}>
      <Container maxWidth="lg">
        <Toolbar disableGutters sx={{ gap: { xs: 0.5, sm: 3 }, minHeight: { xs: 60, sm: 64 }, flexWrap: 'wrap', py: { xs: 0.75, sm: 0 } }}>
          <Typography variant="body1" fontWeight={700} sx={{ mr: 'auto', width: { xs: '100%', sm: 'auto' } }}>Fantasy assistant</Typography>
          <Stack component="nav" aria-label="Main navigation" direction="row" gap={0.5}>
            <Button component={RouterLink} to="/" color={home ? 'primary' : 'inherit'} aria-current={home ? 'page' : undefined}>My week</Button>
            <Button component={RouterLink} to="/past" color={location.pathname.startsWith('/past') ? 'primary' : 'inherit'}
              aria-current={location.pathname.startsWith('/past') ? 'page' : undefined}>Past advice</Button>
            <Button id="more-button" aria-controls={menuAnchor ? 'more-menu' : undefined} aria-haspopup="true"
              aria-expanded={Boolean(menuAnchor)} onClick={event => setMenuAnchor(event.currentTarget)} endIcon={<ExpandMoreIcon />}
              color={moreItems.some(item => item.to === location.pathname) ? 'primary' : 'inherit'}>More</Button>
          </Stack>
          <Menu id="more-menu" anchorEl={menuAnchor} open={Boolean(menuAnchor)} onClose={() => setMenuAnchor(null)}
            slotProps={{ list: { 'aria-labelledby': 'more-button' } }}>
            {moreItems.map(item => <MenuItem key={item.to} component={RouterLink} to={item.to}
              selected={location.pathname === item.to} onClick={() => setMenuAnchor(null)}>{item.label}</MenuItem>)}
          </Menu>
        </Toolbar>
      </Container>
    </AppBar>
    {(active || state.submitting) && <LinearProgress aria-label="Update or research in progress" />}
    <Container component="main" maxWidth="lg" id="main-content" tabIndex={-1} ref={main}
      sx={{ py: { xs: 2.5, sm: 3 }, outline: 'none' }}>
      <Stack spacing={2} mb={state.error || needsRetry || active || state.pollingError || state.recoveryWarning ? 2 : 0}>
        {state.recoveryWarning && <Alert severity="warning">{state.recoveryWarning}</Alert>}
        {state.error && !state.authRequired && <Alert severity="error" onClose={operations.dismissError}
          action={!state.status ? <Button color="inherit" onClick={() => void operations.connect()} disabled={state.connecting}>Reconnect</Button> : undefined}>
          {state.error}
        </Alert>}
        {needsRetry && !(home && state.pending?.kind === 'collect') && <Alert severity="warning"
          action={<Button color="inherit" disabled={state.authRequired || state.connecting} onClick={() => void operations.retry()}>
            {state.pending?.kind === 'collect' ? 'Retry update' : 'Retry comparison'}
          </Button>}>
          {state.pending?.kind === 'collect' ? 'The advice update' : 'The research request'} was not confirmed.
          Retry the same request to recover its result. New requests are paused.
        </Alert>}
        {active && !(home && state.jobs.some(job => activeJob(job) && job.kind === 'collect')) && <Alert severity="info">
          {state.jobs.some(job => activeJob(job) && job.kind === 'evaluate') ? 'Research comparison in progress. My week continues to use the baseline.' : 'Updating advice…'}
        </Alert>}
        {state.pollingError && <Alert severity="warning" action={<Button color="inherit" onClick={() => void operations.poll()}>Check status</Button>}>
          Cannot check update progress. Known work is preserved. {state.pollingError}
        </Alert>}
      </Stack>
      <RouteErrorBoundary key={location.pathname}><Suspense fallback={<Loading />}><Routes>
        <Route path="/" element={<Week />} />
        <Route path="/past" element={<History />} />
        <Route path="/past/:id" element={<HistoricalAdvice />} />
        <Route path="/research" element={<Research />} />
        <Route path="/data" element={<DataService />} />
        <Route path="/connection" element={<Connection />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes></Suspense></RouteErrorBoundary>
      <Box component="footer" sx={{ mt: 4, pt: 2, borderTop: 1, borderColor: 'divider' }}>
        <Typography variant="caption" color="text.secondary">Advisory only. Projections are estimates, not guaranteed results or a proven competitive edge.</Typography>
      </Box>
    </Container>
    <AuthDialog />
  </>;
}
