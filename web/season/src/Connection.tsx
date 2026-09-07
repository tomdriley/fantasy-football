import { useState } from 'react';
import { Alert, Box, Button, Dialog, DialogContent, DialogTitle, Stack, TextField, Typography } from '@mui/material';
import { useLocation, useNavigate } from 'react-router-dom';
import { useOperations } from './hooks.tsx';

function ConnectionForm() {
  const { operations, state } = useOperations();
  const [token, setToken] = useState('');
  return <Stack component="form" spacing={2} onSubmit={event => {
    event.preventDefault();
    const submitted = token;
    setToken('');
    void operations.connect(submitted);
  }}>
    <Typography variant="body2" color="text.secondary">
      Enter the API token configured by the service owner. It stays in memory only and is cleared on reload or disconnect.
    </Typography>
    {state.pending && <Alert severity="info">Your unconfirmed request is preserved. Connecting will not start another update; retry it after connecting.</Alert>}
    <TextField id="api-token" label="API token" type="password" autoComplete="off" value={token}
      onChange={event => setToken(event.target.value)} fullWidth required autoFocus
      slotProps={{ htmlInput: { spellCheck: false, autoCapitalize: 'none' } }} />
    {state.error && <Alert severity="error">{state.error}</Alert>}
    <Button type="submit" variant="contained" disabled={state.connecting || !token.trim()}>
      {state.connecting ? 'Connecting…' : 'Connect'}
    </Button>
  </Stack>;
}

export function AuthDialog() {
  const { state } = useOperations();
  const location = useLocation();
  return <Dialog open={state.authRequired && location.pathname !== '/connection'} fullWidth maxWidth="xs"
    disableEscapeKeyDown aria-labelledby="connection-title">
    <DialogTitle id="connection-title">Connect to your advice</DialogTitle>
    <DialogContent><Box pt={1}><ConnectionForm /></Box></DialogContent>
  </Dialog>;
}

export function Connection() {
  const { operations, state } = useOperations();
  const navigate = useNavigate();
  return <Stack spacing={2.5} maxWidth={520}>
    <Typography variant="h1">Connection</Typography>
    {state.status && !state.authRequired ? <>
      <Alert severity="success">{state.status.mode === 'local-demo' ? 'Connected to the local demo. No token is required.' : 'Connected to the token-protected API.'}</Alert>
      <Typography variant="body2" color="text.secondary">Tokens are never stored in browser storage, URLs or saved request metadata.</Typography>
      {state.status.mode === 'token-protected' && <Button variant="outlined" onClick={operations.disconnect}>Disconnect and clear token</Button>}
      <Button onClick={() => navigate('/')} sx={{ alignSelf: 'flex-start' }}>Back to My week</Button>
    </> : <ConnectionForm />}
  </Stack>;
}
