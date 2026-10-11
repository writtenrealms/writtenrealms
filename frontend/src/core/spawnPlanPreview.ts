export interface MobDefinitionPreviewData {
  id: number;
  name: string;
  slug: string;
  type: string;
  description?: string;
  room_description?: string;
  base_properties: Record<string, unknown>;
  attributes: Record<string, unknown>;
  traits?: Record<string, unknown>;
  randomization?: Record<string, unknown>;
}

export interface SpawnPlanPreviewData {
  entries: {
    slug: string;
    name: string;
    is_active: boolean;
    conditional: boolean;
    count: number | string | { value?: number; min?: number; max?: number };
    target: string;
    target_name: string;
    pooled: boolean;
    sources: { ref: string; name?: string; mob_id?: number; weight: number }[];
  }[];
  mobs: Record<number, MobDefinitionPreviewData>;
}

export function formatSpawnCount(count: SpawnPlanPreviewData['entries'][number]['count']): string {
  if (typeof count === 'number' || typeof count === 'string') return String(count);
  if (count.value !== undefined) return String(count.value);
  if (count.min === undefined && count.max === undefined) return '1';
  const min = count.min ?? 0;
  const max = count.max ?? min;
  return min === max ? String(min) : `${min}–${max}`;
}
