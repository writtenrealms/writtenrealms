export type AccessMode = 'open' | 'invite_only' | 'closed';
export const accessLabels: Record<AccessMode, string> = {
  open: 'Open', invite_only: 'Invite only', closed: 'Closed',
};

export interface StaffAccessPolicy {
  access_mode: AccessMode;
  deployment_restricted: boolean;
  deployment_allowlist_count: number | null;
}

export function effectiveAccessMode(settings: StaffAccessPolicy): AccessMode {
  if (settings.access_mode === 'closed') return 'closed';
  return settings.deployment_restricted ? 'invite_only' : settings.access_mode;
}

export function staffAccessOptions(deploymentRestricted: boolean): { value: AccessMode; label: string; description: string }[] {
  return [
    { value: 'open', label: deploymentRestricted ? 'Use deployment invitations' : 'Open',
      description: deploymentRestricted ? 'Staff and emails invited in deployment settings.' : 'Anyone can sign in and play.' },
    { value: 'invite_only', label: deploymentRestricted ? 'Limit to whitelist' : 'Invite only',
      description: deploymentRestricted ? 'Staff and emails on both invitation lists.' : 'Only staff and whitelisted emails.' },
    { value: 'closed', label: 'Closed', description: 'Only staff can sign in and play.' },
  ];
}

export interface StaffAudit {
  id: number;
  actor: string | null;
  action: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface StaffOverview {
  generated_at: string;
  cache_seconds: number;
  counts: {
    users: number; signups_24h: number; active_users_24h: number;
    characters: number; online_characters: number; online_users: number;
    root_worlds: number; public_worlds: number; running_worlds: number;
  };
  activity: { date: string; signups: number; active_users: number }[];
  online_worlds: {
    id: number; name: string; root_world_id: number; state: string;
    online_characters: number; maintenance_mode: boolean; changed_at: string;
  }[];
  online_characters: {
    id: number; name: string; level: number; user_id: number; username: string;
    world_id: number; world_name: string; root_world_id: number;
    last_connection_ts: number | string | null; last_action_ts: number | string | null;
  }[];
  limits: { online_worlds: number; online_characters: number };
}

export interface StaffJobs {
  generated_at: string;
  cache_seconds: number;
  broker: {
    status: string; detail: string;
    queues: { name: string; ready: number | null; consumers: number | null; unacknowledged: number | null }[];
  };
  workers: {
    status: string; detail: string;
    items: { name: string; active: number | null; reserved: number | null; scheduled: number | null; tasks_truncated?: boolean;
      tasks: { id: string; name: string; state: string; eta: string | null; started_at: string | number | null }[] }[];
  };
  periodic: { id: string; task: string; schedule: string; manual_action: string | null }[];
  durable: {
    detail?: string;
    scheduled_triggers: DurableJobs;
    prepared_actions: DurableJobs;
    event_outbox: { pending: number; due: number; retrying: number; oldest_due_at: string | null };
  };
  actions: { id: string; label: string; description: string }[];
}
interface DurableJobs {
  pending: number; due: number; failed: number;
  recent_failures: { id: number; world_id: number; failure_code: string; completed_at: string }[];
}

export function staffTime(value: string | number | null | undefined): string {
  if (!value) return '—';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

export function staffError(error: unknown, fallback: string): string {
  const data = (error as { response?: { data?: unknown } })?.response?.data;
  if (typeof data === 'object' && data !== null) {
    const values = Object.values(data).flat().filter(value => typeof value === 'string');
    if (values.length) return values.join(' ');
  }
  return fallback;
}

export function chartHeight(value: number, maximum: number): string {
  if (value <= 0 || maximum <= 0) return '0%';
  return `${Math.max(2, Math.min(100, value / maximum * 100))}%`;
}

export function changedStaffSettings<T extends Record<string, unknown>>(draft: T, current: Record<string, unknown>): Partial<T> {
  return Object.fromEntries(Object.entries(draft).filter(([key, value]) => value !== current[key])) as Partial<T>;
}
