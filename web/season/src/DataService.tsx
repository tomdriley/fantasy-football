import { useEffect, useState } from 'react';
import {
  Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Chip, Divider,
  MenuItem, Paper, Stack, TextField, Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import { snapshotPath } from './api.ts';
import { LoadError, Loading } from './components.tsx';
import { useOperations, useRemote } from './hooks.tsx';
import { dateTime } from './presentation.ts';
import type { Snapshot, SnapshotDetail } from './types.ts';

function JsonView({ value }: { value: unknown }) {
  return <Box component="pre" sx={{ m: 0, fontSize: '0.75rem', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
    {JSON.stringify(value, null, 2)}
  </Box>;
}

export function DataService() {
  const { operations, state } = useOperations();
  const snapshots = useRemote<{ items: Snapshot[] }>('/api/v1/snapshots?limit=20');
  const [selectedId, setSelectedId] = useState('');
  const [showSchema, setShowSchema] = useState(false);
  const detail = useRemote<SnapshotDetail>(selectedId ? snapshotPath(selectedId) : null);
  const schema = useRemote<unknown>(showSchema ? '/api/v1/openapi.json' : null);
  useEffect(() => {
    if (!selectedId && snapshots.data?.items.length) setSelectedId(snapshots.data.items[0].id);
  }, [snapshots.data, selectedId]);
  const saved = detail.data?.snapshot.id === selectedId ? detail.data.snapshot : null;
  return <Stack spacing={2.5}>
    <Box>
      <Typography variant="h1">Data and service</Typography>
      <Typography variant="body2" color="text.secondary" mt={0.75}>Technical details, source provenance and durable update jobs.</Typography>
    </Box>
    <Button variant="outlined" onClick={() => void operations.connect()} disabled={state.connecting} sx={{ alignSelf: 'flex-start' }}>Refresh service details</Button>
    {state.status && <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="h2" mb={1}>Service</Typography>
      <Typography variant="body2">{state.status.mode === 'local-demo' ? 'Local demo' : 'Token-protected API'} · Worker {state.status.worker.running ? 'running' : 'not running'}</Typography>
      <Typography variant="body2">{state.status.jobs.queued} queued · {state.status.jobs.running} running at last service check</Typography>
      <Typography variant="body2" color="text.secondary" mt={1}>
        {state.status.storage.unique_payloads} unique payloads · {state.status.storage.raw_bytes.toLocaleString()} raw bytes · {state.status.storage.compressed_bytes.toLocaleString()} compressed bytes
      </Typography>
    </Paper>}
    <Box component="section">
      <Typography variant="h2" mb={1}>Recent jobs</Typography>
      {state.pollingError && <LoadError message={state.pollingError} retry={() => void operations.poll()} />}
      {!state.jobs.length && <Typography variant="body2">No recent jobs.</Typography>}
      {state.jobs.map(job => <Accordion key={job.id} disableGutters variant="outlined">
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Stack gap={1} direction="row" alignItems="center" flexWrap="wrap">
            <Typography variant="body2">{job.kind === 'collect' ? 'Advice update' : 'Research analysis'} · {dateTime(job.created_at_ms)}</Typography>
            <Chip size="small" variant="outlined" label={job.status} color={job.status === 'failed' ? 'error' : 'default'} />
          </Stack>
        </AccordionSummary>
        <AccordionDetails><JsonView value={job} /></AccordionDetails>
      </Accordion>)}
    </Box>
    <Divider />
    <Typography variant="h2">Saved input provenance</Typography>
    {snapshots.error && <LoadError message={snapshots.error} retry={operations.refresh} />}
    <TextField select label="Snapshot" value={selectedId} disabled={!snapshots.data?.items.length} onChange={event => setSelectedId(event.target.value)}>
      {!snapshots.data?.items.length && <MenuItem value="">No snapshots</MenuItem>}
      {snapshots.data?.items.map(snapshot => <MenuItem key={snapshot.id} value={snapshot.id}>
        Week {snapshot.week ?? 'unknown'} · {dateTime(snapshot.finished_at_ms ?? snapshot.started_at_ms)} · {snapshot.status}
      </MenuItem>)}
    </TextField>
    {detail.error && <LoadError message={detail.error} retry={operations.refresh} />}
    {detail.loading && !saved && <Loading />}
    {saved && <>
      <Typography variant="body2">Snapshot: {saved.id}</Typography>
      {!!saved.errors?.length && <Alert severity="error">{saved.errors.join(' ')}</Alert>}
      <Typography variant="body2" color="text.secondary">Capture engine: {saved.engine_fingerprint || 'Not recorded'}. Source ages below are measured at capture, not now.</Typography>
      {saved.observations?.map(observation => <Accordion key={observation.id} disableGutters variant="outlined">
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Box>
            <Typography variant="body2" fontWeight={600}>{observation.role}</Typography>
            <Typography variant="caption" color="text.secondary">
              {observation.origin === 'archive_reuse' ? 'Archive reuse' : observation.origin === 'network' ? 'Network' : observation.origin}
              {' · '}Received {dateTime(observation.received_at_ms)} · Age budget {observation.max_age_seconds}s
            </Typography>
          </Box>
        </AccordionSummary>
        <AccordionDetails><JsonView value={observation} /></AccordionDetails>
      </Accordion>)}
      {!saved.observations?.length && <Typography variant="body2">No source observations recorded.</Typography>}
    </>}
    <Accordion disableGutters variant="outlined" expanded={showSchema} onChange={(_, expanded) => setShowSchema(expanded)}>
      <AccordionSummary expandIcon={<ExpandMoreIcon />}><Typography variant="body2">Versioned API schema</Typography></AccordionSummary>
      <AccordionDetails>
        {schema.loading && <Loading />}
        {schema.error && <LoadError message={schema.error} retry={operations.refresh} />}
        {schema.data !== null && <JsonView value={schema.data} />}
      </AccordionDetails>
    </Accordion>
  </Stack>;
}
