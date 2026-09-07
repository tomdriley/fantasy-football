import { useEffect, useState } from 'react';
import { Alert, Box, Button, Divider, MenuItem, Paper, Stack, TextField, Typography } from '@mui/material';
import { snapshotPath } from './api.ts';
import { Lineup, LoadError, Loading, Pickups } from './components.tsx';
import { useOperations, useRemote } from './hooks.tsx';
import { operationBlocked } from './operations.ts';
import { dateTime, safeEvaluations, signedPoints } from './presentation.ts';
import type { Evaluation, LineupSlot, Snapshot } from './types.ts';

export function Research() {
  const { operations, state } = useOperations();
  const snapshots = useRemote<{ items: Snapshot[] }>('/api/v1/snapshots?limit=20');
  const [snapshotId, setSnapshotId] = useState('');
  const [evaluationId, setEvaluationId] = useState('');
  const [policyId, setPolicyId] = useState('');
  const [floor, setFloor] = useState('2');
  const evaluations = useRemote<{ items: Evaluation[] }>(snapshotId ? `${snapshotPath(snapshotId)}/evaluations` : null);
  const selected = snapshots.data?.items.find(snapshot => snapshot.id === snapshotId);
  const reports = safeEvaluations(evaluations.data?.items || [], snapshotId);
  const evaluation = reports.find(report => report.id === evaluationId) || reports[0];
  const policies = evaluation?.report.policies || [];
  const policy = policies.find(item => item.id === policyId) || policies[0];
  const validFloor = floor.trim() !== '' && Number.isFinite(Number(floor)) && Number(floor) >= 0;
  const busy = operationBlocked(state);

  useEffect(() => {
    if (!snapshotId && snapshots.data?.items.length) {
      setSnapshotId((snapshots.data.items.find(snapshot => snapshot.status === 'complete') || snapshots.data.items[0]).id);
    }
  }, [snapshots.data, snapshotId]);

  const lineup: LineupSlot[] = policy?.recommendation.lineup.map(slot => ({
    ...slot, label: slot.slot, position: null, status: null, kickoff_ms: null,
    locked_at_capture: slot.locked, current_player_id: null, current_player_name: null, change: slot.player_id ? 'same' : 'empty',
  })) || [];

  return <Stack spacing={2.5}>
    <Box>
      <Typography variant="h1">Research lab</Typography>
      <Typography variant="body2" color="text.secondary" mt={0.75}>Experimental comparisons on archived inputs, separate from My week.</Typography>
    </Box>
    <Alert severity="info">
      Different suggestions are not demonstrated improvements. No experiment replaces your baseline advice or makes changes in Sleeper.
    </Alert>
    {snapshots.error && <LoadError message={snapshots.error} retry={operations.refresh} />}
    {snapshots.loading && !snapshots.data && <Loading />}
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack component="form" spacing={2} onSubmit={event => {
        event.preventDefault();
        if (!validFloor || selected?.status !== 'complete' || busy) return;
        setEvaluationId('');
        setPolicyId('');
        void operations.startResearch(snapshotId, Number(floor));
      }}>
        <TextField select label="Archived inputs" value={snapshotId} onChange={event => {
          setSnapshotId(event.target.value); setEvaluationId(''); setPolicyId('');
        }} fullWidth disabled={!snapshots.data?.items.length}>
          {!snapshots.data?.items.length && <MenuItem value="">No archived inputs</MenuItem>}
          {snapshots.data?.items.map(snapshot => <MenuItem key={snapshot.id} value={snapshot.id}>
            Week {snapshot.week ?? 'unknown'} · {dateTime(snapshot.finished_at_ms ?? snapshot.started_at_ms)} · {snapshot.status}
          </MenuItem>)}
        </TextField>
        <TextField label="Experimental minimum pickup gain" type="number" value={floor}
          onChange={event => setFloor(event.target.value)} error={!validFloor}
          helperText="Points this week. User-selected and uncalibrated; not a modeled future roster cost."
          slotProps={{ htmlInput: { min: 0, step: 'any' } }} />
        <Typography variant="body2" color="text.secondary">
          Compares the expected-points baseline with a pickup-floor experiment using exactly the same saved inputs.
          This does not fetch current information.
        </Typography>
        <Button variant="contained" type="submit" sx={{ alignSelf: 'flex-start' }}
          disabled={busy || !validFloor || selected?.status !== 'complete'}>Compare saved inputs</Button>
        {selected && selected.status !== 'complete' && <Alert severity="warning">
          Incomplete or invalid inputs cannot be compared. Choose a completed update.
        </Alert>}
      </Stack>
    </Paper>
    {evaluations.error && <LoadError message={evaluations.error} retry={operations.refresh} />}
    {evaluations.loading && !evaluations.data && <Loading />}
    {!evaluations.loading && !reports.length && <Typography variant="body2" color="text.secondary">
      No saved analysis for these inputs. A complete update is required for comparison.
    </Typography>}
    {evaluation && selected?.status === 'complete' && <>
      <Divider />
      <Typography variant="h2">Saved research results</Typography>
      <TextField select label="Saved analysis" value={evaluation.id} onChange={event => { setEvaluationId(event.target.value); setPolicyId(''); }}>
        {reports.map(report => <MenuItem key={report.id} value={report.id}>
          {dateTime(report.recorded_at_ms)} · {report.report.policies.length > 1 ? 'Comparison' : 'Baseline only'}
        </MenuItem>)}
      </TextField>
      <Typography variant="body2" color="text.secondary">
        {evaluation.report.interpretation} Archived decision time: {dateTime(evaluation.report.decision_at_ms)}.
      </Typography>
      {evaluation.report.same_engine_as_capture === false && <Alert severity="warning">
        The engine changed since these inputs were saved. This replay is not the original historical result.
      </Alert>}
      {evaluation.report.disagreements.map(difference => <Paper variant="outlined" sx={{ p: 2 }} key={difference.challenger}>
        <Typography variant="h3">{policies.find(item => item.id === difference.challenger)?.name || 'Experimental policy'}</Typography>
        <Typography variant="body2" mt={0.75}>
          {difference.lineup_changed ? 'Different lineup' : 'Same lineup'} · {difference.pickups_changed ? 'Different pickup options' : 'Same pickup options'}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {signedPoints(difference.projected_lineup_delta)} snapshot projected points versus baseline — not measured benefit.
        </Typography>
      </Paper>)}
      {!evaluation.report.disagreements.length && <Alert severity="info">This saved analysis contains only the baseline. Run a comparison above to add the experimental pickup floor.</Alert>}
      {policy && <>
        <TextField select label="Policy result to inspect" value={policy.id} onChange={event => setPolicyId(event.target.value)}>
          {policies.map(item => <MenuItem value={item.id} key={item.id}>{item.role === 'baseline' ? 'Baseline' : 'Experimental only'} · {item.name} v{item.version}</MenuItem>)}
        </TextField>
        <Typography variant="body2">
          {policy.role === 'baseline' ? 'Archived baseline' : 'Experimental only'} · Parameters: {JSON.stringify(policy.parameters)}
        </Typography>
        <Lineup slots={lineup} total={policy.recommendation.projected_total} historical />
        <Pickups pickups={policy.recommendation.pickups} historical />
        <Box component="ul" sx={{ pl: 2.5, m: 0 }}>
          {[...policy.recommendation.warnings, ...policy.recommendation.unfilled.map(slot => `Unfilled slot: ${slot}`),
            ...policy.recommendation.assumptions].map((note, index) => <Typography component="li" variant="body2" key={index}>{note}</Typography>)}
        </Box>
      </>}
    </>}
  </Stack>;
}
