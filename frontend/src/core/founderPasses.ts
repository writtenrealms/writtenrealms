export interface FounderPass {
  id: number;
  world_id: number;
  world_name: string;
  kind: 'direct' | 'gift';
  claimed_at: string | null;
  claim_reason: string;
  claim_deadline: string | null;
  can_claim: boolean;
  can_transfer: boolean;
  can_invite: boolean;
  invited_email: string | null;
  transfer_email: string | null;
  transfer_expires_at: string | null;
}

export function passStatus(pass: FounderPass): string {
  if (pass.claimed_at) return pass.kind === 'gift' ? 'Gifted · Claimed' : 'Claimed';
  return pass.transfer_email ? 'Transfer awaiting confirmation' : 'Ready to claim';
}

export function accountActionError(error: any): string {
  const data = error?.response?.data;
  if (typeof data?.detail === 'string') return data.detail;
  if (typeof data?.email === 'string') return data.email;
  if (Array.isArray(data?.email)) return data.email.join(' ');
  if (Array.isArray(data)) return data.join(' ');
  return 'Could not complete the request. Please try again.';
}
