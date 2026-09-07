import { Alert, Box, Button, Chip, List, ListItem, ListItemButton, Paper, Stack, Typography } from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import { Link as RouterLink, useParams } from 'react-router-dom';
import { advicePath } from './api.ts';
import { Evidence, Lineup, LoadError, Loading, Pickups } from './components.tsx';
import { useOperations, useRemote } from './hooks.tsx';
import { dateTime, historyItems } from './presentation.ts';
import type { Advice, Snapshot } from './types.ts';

export function History() {
  const { operations, state } = useOperations();
  const remote = useRemote<{ items: Snapshot[] }>('/api/v1/snapshots?limit=20');
  const items = historyItems(remote.data?.items || [], state.jobs);
  return <Stack spacing={2}>
    <Box>
      <Typography variant="h1">Past advice</Typography>
      <Typography variant="body2" color="text.secondary" mt={0.75}>Review recent saved reports and unsuccessful updates. These do not change My week.</Typography>
    </Box>
    {remote.error && <LoadError message={remote.error} retry={operations.refresh} />}
    {remote.loading && !remote.data && <Loading />}
    <Paper variant="outlined">
      <List disablePadding aria-label="Past updates">
        {items.map((item, index) => {
          const content = <Stack direction="row" alignItems="flex-start" justifyContent="space-between" gap={1} width="100%">
            <Box>
              <Typography fontWeight={600} variant="body2">
                {item.week ? `Week ${item.week} · ` : ''}{dateTime(item.time)}
              </Typography>
              {item.error && <Typography variant="body2" color="text.secondary" mt={0.5}>{item.error}</Typography>}
            </Box>
            <Chip size="small" variant="outlined" color={item.status === 'Update failed' ? 'error' : 'default'} label={item.status} />
          </Stack>;
          return <ListItem key={item.key} disablePadding={Boolean(item.snapshotId)} divider={index < items.length - 1}>
            {item.snapshotId ? <ListItemButton component={RouterLink} to={`/past/${encodeURIComponent(item.snapshotId)}`} sx={{ py: 2 }}>
              {content}
            </ListItemButton> : content}
          </ListItem>;
        })}
      </List>
      {!remote.loading && !items.length && <Typography sx={{ p: 2 }} variant="body2">No saved updates yet. Start with Update advice on My week.</Typography>}
    </Paper>
    <Button component={RouterLink} to="/" startIcon={<ArrowBackIcon />} sx={{ alignSelf: 'flex-start' }}>Back to My week</Button>
  </Stack>;
}

export function HistoricalAdvice() {
  const { id } = useParams();
  const { operations } = useOperations();
  const remote = useRemote<Advice>(advicePath(id));
  const advice = remote.data?.mode === 'historical' && remote.data.snapshot?.id === id ? remote.data : null;
  return <Stack spacing={2.5}>
    <Button component={RouterLink} to="/past" startIcon={<ArrowBackIcon />} sx={{ alignSelf: 'flex-start' }}>Past advice</Button>
    <Typography variant="h1">Historical report</Typography>
    <Alert severity="warning">Historical report — do not treat as current advice. Return to My week for the latest baseline.</Alert>
    {remote.error && <LoadError message={remote.error} retry={operations.refresh} />}
    {remote.loading && !remote.data && <Loading />}
    {remote.data && !advice && <Alert severity="error">This response does not match the selected historical report. No recommendations are displayed.</Alert>}
    {advice && <>
      <Box>
        <Typography variant="h2">{advice.league.team_name} · Week {advice.snapshot?.week ?? 'unknown'}</Typography>
        <Typography variant="body2" color="text.secondary" mt={0.5}>Saved {dateTime(advice.snapshot?.as_of_ms)} · {advice.snapshot?.season}</Typography>
      </Box>
      {advice.snapshot?.status !== 'complete' && <Alert severity="error">
        This update failed to obtain usable advice. {advice.latest_attempt?.errors.join(' ')}
      </Alert>}
      <Typography variant="body2">{advice.freshness.message}</Typography>
      <Lineup slots={advice.lineup} total={advice.summary.projected_total} historical />
      <Pickups pickups={advice.pickups} historical />
      <Evidence advice={advice} />
      <Button component={RouterLink} to="/" variant="outlined" sx={{ alignSelf: 'flex-start' }}>Return to My week</Button>
    </>}
  </Stack>;
}
