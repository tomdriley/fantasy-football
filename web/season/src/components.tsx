import {
  Accordion, AccordionDetails, AccordionSummary, Alert, Box, Button, Chip,
  Link, List, ListItem, ListItemText, Paper, Skeleton, Stack, Table, TableBody,
  TableCell, TableContainer, TableHead, TableRow, Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import type { Advice, LineupRepair, LineupSlot, ManagerDecision, ManagerDecisions, Stream } from './types.ts';
import { dateTime, points, signedPoints } from './presentation.ts';

export function Loading() {
  return <Stack spacing={1} aria-label="Loading advice" role="status">
    <Typography color="text.secondary">Loading…</Typography>
    <Skeleton variant="rounded" height={90} />
    <Skeleton variant="rounded" height={150} />
  </Stack>;
}

export function LoadError({ message, retry }: { message: string; retry: () => void }) {
  return <Alert severity="error" action={<Button color="inherit" onClick={retry}>Try again</Button>}>
    {message}
  </Alert>;
}

export function Lineup({ slots, total, live = false, historical = false }: {
  slots: LineupSlot[]; total: number | null; live?: boolean; historical?: boolean;
}) {
  return <Box component="section" aria-labelledby="lineup-title">
    <Stack direction="row" justifyContent="space-between" alignItems="baseline" gap={1} flexWrap="wrap" mb={1}>
      <Typography id="lineup-title" variant="h2">
        {historical ? 'Lineup in this report' : live ? 'Your suggested lineup' : 'Previous lineup · reference only'}
      </Typography>
      {total !== null && <Typography variant="body2" color="text.secondary">
        {points(total)} projected points
      </Typography>}
    </Stack>
    <TableContainer component={Paper} variant="outlined">
      <Table size="small" aria-label={live ? 'Suggested lineup' : 'Saved lineup, not current advice'} sx={{ tableLayout: 'fixed' }}>
        <caption>Projections and locks are from the saved update, not live game data. — means unknown, not zero.</caption>
        <TableHead>
          <TableRow>
            <TableCell sx={{ width: { xs: '19%', sm: '13%' } }}>Slot</TableCell>
            <TableCell>Player</TableCell>
            <TableCell align="right" sx={{ width: { xs: '22%', sm: '18%' } }}>Proj. pts</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {slots.map(slot => <TableRow key={slot.slot_index}>
            <TableCell component="th" scope="row" sx={{ fontWeight: 600, verticalAlign: 'top', pt: 1.5 }}>
              {slot.label || slot.slot}
            </TableCell>
            <TableCell>
              <Stack direction="row" alignItems="center" gap={0.75} flexWrap="wrap">
                <Typography variant="body2" fontWeight={600}>{slot.name || (slot.player_id ? 'Name unavailable' : 'Unfilled slot')}</Typography>
                {live && (slot.change === 'start' || slot.change === 'move') && <Chip size="small" variant="outlined"
                  label={slot.change === 'start' ? 'Change' : 'Slot move'} color="primary" />}
              </Stack>
              <Typography variant="caption" component="div" color="text.secondary">
                {[slot.position, slot.status, slot.locked_at_capture ? 'Locked when saved' : null,
                  slot.kickoff_ms ? dateTime(slot.kickoff_ms) : null].filter(Boolean).join(' · ') || 'Player details unavailable'}
              </Typography>
              {live && slot.change === 'start' && slot.current_player_name && <Typography variant="caption" color="text.secondary">
                In place of {slot.current_player_name}
              </Typography>}
            </TableCell>
            <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>{points(slot.projected_points)}</TableCell>
          </TableRow>)}
          {!slots.length && <TableRow><TableCell colSpan={3}>No lineup was saved in this report.</TableCell></TableRow>}
        </TableBody>
      </Table>
    </TableContainer>
    {slots.some(slot => /FLEX/i.test(slot.label || slot.slot)) && <Typography variant="caption" component="p" color="text.secondary" mt={0.75}>
      FLEX can hold an eligible player from more than one position. {live ? 'Follow the full slot arrangement above.' : 'This is the saved slot arrangement, not current guidance.'}
    </Typography>}
  </Box>;
}

export function Recommendations({ decisions }: { decisions: ManagerDecisions }) {
  const combined = decisions.lineup.action === 'repair' && decisions.roster.action === 'repair'
    && decisions.lineup.title === decisions.roster.title
    && decisions.lineup.instruction === decisions.roster.instruction
    && decisions.lineup.reason === decisions.roster.reason;
  const items = combined ? [['Team', decisions.lineup] as const]
    : [['Lineup', decisions.lineup] as const, ['Roster', decisions.roster] as const];
  const labels = { hold: 'Hold', change: 'Act', check: 'Check now', repair: 'Action needed', update: 'Update first' };
  return <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5}>
    {items.map(([area, decision]) => <Paper key={area} variant="outlined" component="section"
      aria-label={`${area} recommendation`} sx={{ p: 2, flex: 1 }}>
      <Stack direction="row" gap={1} alignItems="center" mb={1}>
        <Typography variant="overline" color="text.secondary">{area}</Typography>
        <Chip size="small" label={labels[decision.action]}
          color={decision.action === 'hold' ? 'primary' : 'warning'} variant="outlined" />
      </Stack>
      <Typography variant="h3">{decision.title}</Typography>
      <Typography variant="body2" mt={0.75}>{decision.instruction}</Typography>
      <Typography variant="body2" color="text.secondary" mt={0.75}>{decision.reason}</Typography>
    </Paper>)}
  </Stack>;
}

export function RepairInstructions({ repair }: { repair: LineupRepair }) {
  if (repair.blocked || repair.status !== 'proposed' || !repair.add) return null;
  return <Paper variant="outlined" component="section" aria-label="Required roster repair" sx={{ p: 2 }}>
    <Typography variant="h3">Complete this roster repair first</Typography>
    <Typography variant="body2" mt={0.75}>
      Complete the move before: {dateTime(repair.deadline_ms, true)}
    </Typography>
    <Box component="ol" sx={{ pl: 2.5, mb: 0 }}>
      {repair.instructions.map((instruction, i) => <Typography component="li" variant="body2" key={i} mb={0.5}>
        {instruction}
      </Typography>)}
    </Box>
  </Paper>;
}

export function Pickups({ pickups, historical = false, decision }: {
  pickups: Stream[]; historical?: boolean; decision?: ManagerDecision;
}) {
  const hold = !historical && decision?.action === 'hold';
  return <Accordion disableGutters variant="outlined">
    <AccordionSummary expandIcon={<ExpandMoreIcon />} aria-controls="pickup-details" id="pickup-summary">
      <Typography fontWeight={600}>
        {historical ? 'Pickups in this report' : hold ? 'Pickups considered · no moves recommended' : 'Pickup comparisons'}
        {' '}({pickups.length})
      </Typography>
    </AccordionSummary>
    <AccordionDetails>
      {hold && <Alert severity="info" sx={{ mb: 1.5 }}>Keep your roster. Do not make these swaps right now.</Alert>}
      <Typography variant="body2" color="text.secondary">
        These are one-week projection comparisons, not instructions to add or drop.
        Future player value and acquisition costs are not modeled well enough to approve an optional move.
      </Typography>
      <List disablePadding>
        {pickups.map((pickup, index) => <ListItem key={`${pickup.add.player_id}-${index}`} disableGutters divider>
          <ListItemText
            primary={<Stack direction="row" justifyContent="space-between" gap={1}>
              <Typography variant="body2" fontWeight={600}>{pickup.add.name}</Typography>
              <Typography variant="body2" sx={{ whiteSpace: 'nowrap' }}>{signedPoints(pickup.gain)} projected pts</Typography>
            </Stack>}
            secondary={<>
              {pickup.drop ? `Would require dropping ${pickup.drop.name}. ` : 'No drop included in this comparison. '}
              {hold && 'Not recommended now. '}
              {!hold && [pickup.acquisition, pickup.warning, pickup.note || pickup.reason].filter(Boolean).join(' ')}
            </>}
          />
        </ListItem>)}
      </List>
      {!pickups.length && <Typography variant="body2" mt={1}>No pickup comparisons were identified in this report.</Typography>}
    </AccordionDetails>
  </Accordion>;
}

export function Evidence({ advice }: { advice: Advice }) {
  const notes = [...new Set([...advice.warnings, ...advice.limitations, ...advice.freshness.reasons])];
  if (!notes.length) return null;
  return <Accordion disableGutters variant="outlined">
    <AccordionSummary expandIcon={<ExpandMoreIcon />} aria-controls="notes-details" id="notes-summary">
      <Typography variant="body2">Advice limits and checks ({notes.length})</Typography>
    </AccordionSummary>
    <AccordionDetails>
      <Box component="ul" sx={{ pl: 2, m: 0 }}>
        {notes.map((note, index) => <Typography variant="body2" component="li" key={index} mb={0.75}>{note}</Typography>)}
      </Box>
    </AccordionDetails>
  </Accordion>;
}

export function SleeperHandoff({ live, actionRequired = true }: { live: boolean; actionRequired?: boolean }) {
  return <Box component="section" sx={{ mt: 1.5 }}>
    <Stack direction={{ xs: 'column', sm: 'row' }} alignItems={{ xs: 'flex-start', sm: 'center' }} gap={1.5}>
      <Button component={Link} href="https://sleeper.com/" target="_blank" rel="noopener noreferrer"
        variant="outlined" endIcon={<OpenInNewIcon />}>Open Sleeper</Button>
      <Typography variant="body2" color="text.secondary">
        {live ? actionRequired ? 'Make only the recommended changes in Sleeper, then update advice here.'
          : 'No lineup or roster changes are recommended right now. Use Sleeper for player-status checks.'
          : 'View your team in Sleeper; update before following saved advice.'} Nothing is applied automatically.
      </Typography>
    </Stack>
  </Box>;
}
