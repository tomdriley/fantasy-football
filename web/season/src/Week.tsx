import { useEffect } from 'react';
import {
  Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Chip, List,
  ListItem, ListItemText, Paper, Stack, Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import RefreshIcon from '@mui/icons-material/Refresh';
import { advicePath } from './api.ts';
import { useClock, useOperations, useRemote } from './hooks.tsx';
import { activeJob, operationBlocked } from './operations.ts';
import { ageLabel, availabilityChecks, confirmedAdvice, dateTime, displayedActions, failedUpdateAfterAdvice } from './presentation.ts';
import { Evidence, Lineup, LoadError, Loading, Pickups, SleeperHandoff } from './components.tsx';
import type { Advice } from './types.ts';

export function Week() {
  const { operations, state } = useOperations();
  const remote = useRemote<Advice>(advicePath());
  const clock = useClock();
  const advice = remote.data?.mode === 'current' ? remote.data : null;
  const elapsed = Math.max(0, clock - remote.clockAnchorAt);
  const collecting = state.jobs.some(job => activeJob(job) && job.kind === 'collect')
    || (state.submitting && state.pending?.kind === 'collect')
    || Boolean(state.tracking && !state.jobs.some(job => job.id === state.tracking && job.kind === 'evaluate'));
  const pending = state.pending?.kind === 'collect' && !state.submitting;
  const updating = collecting || Boolean(state.pending?.kind === 'collect');
  const usable = Boolean(advice && !remote.error && !updating
    && confirmedAdvice(advice, elapsed, remote.dataRevision, state.adviceRevision, state.jobs));
  const latestFailed = Boolean(advice && (failedUpdateAfterAdvice(advice, state.jobs)
    || (advice.latest_attempt && !['complete', 'succeeded', 'running', 'queued'].includes(advice.latest_attempt.status))));
  const connected = Boolean(state.status && !state.authRequired);
  const title = pending ? 'Update not confirmed'
    : collecting ? 'Updating advice…'
    : !connected ? 'Advice unavailable'
    : remote.error ? 'Advice could not be checked'
    : remote.loading && advice && !usable ? 'Checking saved advice…'
    : !advice ? 'Checking your advice…'
    : latestFailed ? 'Your last update failed'
    : !advice.snapshot ? 'Get your first advice'
    : usable ? 'Advice is recent'
    : advice.freshness.state === 'locks_changed' ? 'Lineup locks have changed'
    : 'Update before making changes';
  const message = pending ? 'The response was lost or needs confirmation. Retry the original update; a new request could duplicate it.'
    : collecting ? 'Getting fresh information and checking your team. You can browse past advice while you wait.'
    : !connected ? 'Connect to the service to check your team. Previously saved advice is not current guidance.'
    : remote.error ? 'Use Update advice to get fresh information. Do not act on an unchecked report.'
    : remote.loading && advice && !usable ? 'Confirming that the saved report is still usable. This check does not start a new update.'
    : !advice ? 'Reading the latest saved update.'
    : latestFailed ? 'No new usable advice was obtained. Any previous lineup below is reference only.'
    : !advice.snapshot ? 'Update advice to see your lineup and anything that needs attention.'
    : advice.freshness.usable && !usable ? 'The saved information is no longer fresh enough. Update before making changes.'
    : advice.freshness.message;

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const refresh = () => { operations.refresh(); timer = setTimeout(refresh, 30_000); };
    timer = setTimeout(refresh, 30_000);
    const onFocus = () => operations.refresh();
    window.addEventListener('focus', onFocus);
    return () => { clearTimeout(timer); window.removeEventListener('focus', onFocus); };
  }, [operations]);

  return <Stack spacing={2.5}>
    <Box>
      <Typography variant="h1" mb={0.5}>My week</Typography>
      <Typography variant="body2" color="text.secondary">
        {advice?.league.team_name || state.status?.league.team_name || 'Your team'}
        {advice?.snapshot ? ` · Week ${advice.snapshot.week ?? 'unknown'} · ${advice.snapshot.season ?? ''}` : ''}
        {(advice?.league.name || state.status?.league.name) && ` · ${advice?.league.name || state.status?.league.name}`}
      </Typography>
    </Box>

    <Paper variant="outlined" sx={{ p: { xs: 2, sm: 2.5 } }} component="section" aria-label="Advice status">
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems={{ sm: 'center' }} justifyContent="space-between">
        <Box sx={{ minWidth: 0 }} role="status" aria-live="polite">
          <Chip size="small" variant="outlined" label={usable ? 'Recent' : collecting ? 'Updating' : pending ? 'Needs confirmation' : remote.loading ? 'Checking' : 'Update needed'}
            color={usable ? 'success' : collecting ? 'info' : 'warning'} sx={{ mb: 1 }} />
          <Typography variant="h2">{title}</Typography>
          <Typography variant="body2" color="text.secondary" mt={0.5}>{message}</Typography>
          {advice?.snapshot && <Typography variant="caption" component="p" color="text.secondary" mt={0.5}>
            {ageLabel(advice.snapshot.as_of_ms, advice.now_ms + elapsed)}
          </Typography>}
        </Box>
        <Button variant="contained" startIcon={<RefreshIcon />} sx={{ flexShrink: 0, alignSelf: { xs: 'stretch', sm: 'center' } }}
          disabled={pending ? state.submitting || state.authRequired || state.connecting : operationBlocked(state)}
          onClick={() => void (pending ? operations.retry() : operations.startUpdate())}>
          {state.submitting || collecting ? 'Updating advice…' : pending ? 'Retry update' : 'Update advice'}
        </Button>
      </Stack>
    </Paper>

    {remote.error && <LoadError message={remote.error} retry={operations.refresh} />}
    {remote.data && !advice && <Alert severity="error">The service returned a historical report instead of current advice. No recommendations are displayed.</Alert>}
    {!advice && remote.loading && <Loading />}

    {advice && <>
      <Paper variant="outlined" component="section" sx={{ p: 2 }} aria-labelledby="deadline-title">
        <Typography variant="h2" id="deadline-title">
          {usable ? 'Next lineup deadline' : 'Lineup deadline · verify with an update'}
        </Typography>
        {advice.next_deadline ? <>
          <Typography fontWeight={600} variant="body2" mt={0.75}>{dateTime(advice.next_deadline.at_ms, true)}</Typography>
          <Typography variant="body2" color="text.secondary">
            {advice.next_deadline.players.map(player => player.name).join(', ')}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Save changes before this time; it is the lock deadline, not the time to start checking.
          </Typography>
        </> : <Typography variant="body2" color="text.secondary" mt={0.75}>
          {advice.snapshot ? 'No upcoming lineup deadline is available in this report. Check kickoff times in Sleeper.' : 'Update advice to load your lineup deadlines.'}
        </Typography>}
      </Paper>

      <Box component="section" aria-labelledby="attention-title">
        <Typography variant="h2" id="attention-title" mb={1}>What needs attention</Typography>
        {usable ? <Attention advice={advice} elapsed={elapsed} /> : <Alert severity={latestFailed ? 'error' : 'info'}>
          {latestFailed ? 'Update again before making lineup decisions.' : 'Update first. Saved suggestions are not live instructions.'}
          {!!advice.summary.missing_slots.length && ` The last report had unfilled slots: ${advice.summary.missing_slots.join(', ')}.`}
        </Alert>}
        <SleeperHandoff live={usable} />
      </Box>

      {advice.snapshot && <Lineup slots={advice.lineup} total={advice.summary.projected_total} live={usable} />}
      {usable && <Pickups pickups={advice.pickups} />}
      <Evidence advice={advice} />
    </>}
    {!advice && !remote.loading && connected && !remote.error && <Alert severity="info">No advice is available yet. Use Update advice to get started.</Alert>}
  </Stack>;
}

function Attention({ advice, elapsed }: { advice: Advice; elapsed: number }) {
  const actions = displayedActions(advice, elapsed);
  const mainActions = actions.filter(action => action.kind !== 'availability');
  const availabilityActions = actions.filter(action => action.kind === 'availability');
  const checks = availabilityChecks(advice.injuries, advice.now_ms + elapsed);
  const due = checks.some(check => check.state === 'now');
  const upcoming = checks.find(check => check.state === 'upcoming');
  return <Stack spacing={1.5}>
    <Typography variant="body2">{advice.summary.headline}</Typography>
    {!!advice.summary.missing_slots.length && <Alert severity="warning">
      Lineup incomplete: {advice.summary.missing_slots.join(', ')}. Review these slots in Sleeper.
    </Alert>}
    {!!mainActions.length && <Paper variant="outlined">
      <List disablePadding aria-label="Lineup and roster attention">
        {mainActions.map((action, index) => <ListItem key={action.id} divider={index < mainActions.length - 1} alignItems="flex-start"
          sx={{ display: 'block', py: 1.5 }}>
          <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap">
            <Typography variant="h3">{action.title}</Typography>
            {action.blocked && <Chip size="small" color="warning" label="Blocked · check in Sleeper" />}
          </Stack>
          <Typography variant="body2" color="text.secondary" mt={0.5}>{action.description}</Typography>
          {!!action.instructions.length && <Box component="ul" sx={{ pl: 2.5, mb: 0, mt: 0.75 }}>
            {action.instructions.map((instruction, i) => <Typography component="li" variant="body2" key={i}>{instruction}</Typography>)}
          </Box>}
        </ListItem>)}
      </List>
    </Paper>}
    {(advice.injuries.length > 0 || availabilityActions.length > 0) && <Accordion disableGutters variant="outlined">
      <AccordionSummary expandIcon={<ExpandMoreIcon />} aria-controls="injury-details" id="injury-summary">
        <Box>
          <Typography fontWeight={600} variant="body2">
            {due ? 'Check player availability now' : 'When to check player availability'}
            {' '}({advice.injuries.length || availabilityActions.length})
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {due ? 'Come back now, update advice, and verify who is playing.'
              : upcoming ? `Next check: ${dateTime(upcoming.checkAt, true)}`
              : 'Check game times and player status in Sleeper.'}
          </Typography>
        </Box>
      </AccordionSummary>
      <AccordionDetails>
        <Typography variant="body2" color="text.secondary">
          Questionable means uncertain, not ruled out. No need to bench these players just for that tag.
          Come back around 90 minutes before their games and follow these steps:
        </Typography>
        <Box component="ol" sx={{ pl: 2.5, my: 1 }}>
          <Typography component="li" variant="body2">Choose Update advice here.</Typography>
          <Typography component="li" variant="body2">
            Open Sleeper and check each player's latest status. OUT or inactive means not playing.
          </Typography>
          <Typography component="li" variant="body2">
            If ruled out, review the updated replacement advice and save an eligible replacement in Sleeper before kickoff.
            If the status is still unclear, check the player notes rather than assuming they are out.
          </Typography>
        </Box>
        <List dense disablePadding>
          {checks.length ? checks.map(check => <ListItem disableGutters key={`${check.checkAt}:${check.kickoff}`}>
            <ListItemText
              primary={check.state === 'now' ? 'Check now'
                : check.state === 'locked' ? 'Game started — this slot is locked'
                : check.state === 'unknown' ? 'Check time unavailable — verify in Sleeper'
                : `Come back ${dateTime(check.checkAt, true)}`}
              secondary={<>
                {check.players.map(player => `${player.name} (${player.status})`).join(', ')}
                {check.kickoff !== null && ` · Game starts ${dateTime(check.kickoff, true)}.`}
              </>}
            />
          </ListItem>) : availabilityActions.map(action => <ListItem disableGutters key={action.id}>
            <ListItemText primary={action.title} secondary={[action.description, ...action.instructions].join(' ')} />
          </ListItem>)}
        </List>
        <Typography variant="caption" color="text.secondary">
          No automatic reminder is sent yet. Set a reminder for these check times if needed.
          This app may lag official news, so verify in Sleeper before acting.
        </Typography>
      </AccordionDetails>
    </Accordion>}
  </Stack>;
}
