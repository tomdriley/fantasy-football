export type Job = {
  id: string;
  kind: 'collect' | 'evaluate';
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  created_at_ms: number;
  started_at_ms?: number | null;
  finished_at_ms?: number | null;
  snapshot_id: string | null;
  result?: { snapshot_id?: string; evaluation_id?: string } | null;
  error?: string | null;
};

export type League = { name: string; team_name: string; season: string };
export type Status = {
  mode: 'local-demo' | 'token-protected';
  league: League;
  storage: { unique_payloads: number; raw_bytes: number; compressed_bytes: number };
  jobs: { queued: number; running: number };
  worker: { running: boolean };
  server_time_ms: number;
};

export type Snapshot = {
  id: string;
  status: 'complete' | 'incomplete' | 'invalid' | 'unfinished';
  week: number | null;
  season: string | null;
  started_at_ms: number;
  finished_at_ms: number | null;
};

export type Player = {
  player_id: string;
  name: string;
  position?: string;
  points: number | null;
};
export type Stream = {
  add: Player;
  drop: Player | null;
  gain: number;
  acquisition?: string;
  warning?: string;
  note?: string;
  reason?: string;
  [key: string]: unknown;
};
export type LineupSlot = {
  slot_index: number;
  slot: string;
  label: string;
  player_id: string | null;
  name: string | null;
  position: string | null;
  status: string | null;
  projected_points: number | null;
  kickoff_ms: number | null;
  locked_at_capture: boolean;
  current_player_id: string | null;
  current_player_name: string | null;
  change: 'start' | 'move' | 'same' | 'empty';
};
export type AdviceAction = {
  id: string;
  kind: 'lineup' | 'availability' | 'roster';
  priority: 'required' | 'check';
  title: string;
  description: string;
  instructions: string[];
  player_ids: string[];
  blocked: boolean;
};
export type Advice = {
  mode: 'current' | 'historical';
  now_ms: number;
  league: League;
  snapshot: null | {
    id: string; status: Snapshot['status']; week: number | null;
    season: string | null; as_of_ms: number;
  };
  latest_attempt: null | {
    id: string; status: string; finished_at_ms: number | null; errors: string[];
  };
  freshness: {
    state: 'recent' | 'stale' | 'locks_changed' | 'unavailable' | 'historical' | 'rules_changed';
    usable: boolean;
    age_ms: number | null;
    message: string;
    reasons: string[];
    threshold_seconds: number;
    valid_until_ms: number | null;
  };
  next_deadline: null | { at_ms: number; players: { player_id: string; name: string }[] };
  summary: {
    headline: string; lineup_change_count: number; injury_count: number;
    missing_slots: string[]; projected_total: number | null;
  };
  actions: AdviceAction[];
  lineup: LineupSlot[];
  injuries: {
    player_id: string; name: string; status: string; kickoff_ms: number | null;
    check_at_ms?: number | null; note: string;
  }[];
  pickups: Stream[];
  warnings: string[];
  sleeper_url: string;
  evaluation_id: string | null;
  limitations: string[];
};

export type Recommendation = {
  lineup: {
    slot_index: number; slot: string; player_id: string | null;
    name: string | null; projected_points: number | null; locked: boolean;
  }[];
  projected_total: number | null;
  pickups: Stream[];
  warnings: string[];
  assumptions: string[];
  unfilled: string[];
};
export type Policy = {
  id: string; name: string; version: string; role: 'baseline' | 'shadow-only';
  parameters: Record<string, unknown>;
  recommendation: Recommendation;
};
export type Evaluation = {
  id: string; capture_id: string; recorded_at_ms: number; engine_fingerprint: string;
  report: {
    snapshot_id: string;
    decision_at_ms: number;
    policies: Policy[];
    disagreements: {
      challenger: string; lineup_changed: boolean; pickups_changed: boolean;
      projected_lineup_delta: number;
    }[];
    interpretation: string;
    same_engine_as_capture?: boolean;
  };
};
export type SnapshotDetail = {
  snapshot: Snapshot & {
    errors?: string[];
    engine_fingerprint?: string;
    observations?: {
      id: string; role: string; origin: string; received_at_ms: number;
      max_age_seconds: number; status_code: number | null; error: string | null;
      body_hash: string | null; headers?: Record<string, string>;
    }[];
  };
  league: League;
  latest_evaluation: Evaluation | null;
};
