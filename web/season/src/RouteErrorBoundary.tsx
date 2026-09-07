import { Component } from 'react';
import type { ReactNode } from 'react';
import { Alert, AlertTitle, Button, Stack } from '@mui/material';
import { Link as RouterLink } from 'react-router-dom';

export class RouteErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <Alert severity="error">
      <AlertTitle>This page could not be loaded</AlertTitle>
      Reload to retry the page download. You can still use the navigation above.
      You may need to reconnect after reloading.
      <Stack direction="row" spacing={1} mt={1} flexWrap="wrap">
        <Button color="inherit" variant="outlined" onClick={() => window.location.reload()}>Reload page</Button>
        <Button component={RouterLink} to="/" color="inherit">Go to My week</Button>
      </Stack>
    </Alert>;
  }
}
