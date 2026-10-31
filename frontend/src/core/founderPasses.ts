export interface FounderPass {
  id: number;
  world_id: number;
  world_name: string;
  world_background?: string | null;
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
  if (pass.claimed_at) return '';
  return pass.transfer_email ? 'Transfer awaiting confirmation' : '';
}

export function formatPassClaimDate(value: string): string {
  const date = new Date(value);
  // Match the recipient's local calendar day, including claims near midnight UTC.
  return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'),
    String(date.getDate()).padStart(2, '0')].join('/');
}

export function accountActionError(error: any): string {
  const data = error?.response?.data;
  if (typeof data?.detail === 'string') return data.detail;
  if (typeof data?.email === 'string') return data.email;
  if (Array.isArray(data?.email)) return data.email.join(' ');
  if (Array.isArray(data)) return data.join(' ');
  return 'Could not complete the request. Please try again.';
}
