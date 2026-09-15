import axios from "axios";

export interface PlayerCompletion {
  id: number;
  template_name: string;
  template_slug: string;
  started_at: string;
  completed_at: string;
  clear_time_ms: number;
  time_control: boolean;
}

export interface PlayerCompletionPage {
  results: PlayerCompletion[];
  next: string | null;
  previous: string | null;
}

export const fetchPlayerCompletions = async (
  worldId: string | number, playerId: string | number, next: string | null = null,
): Promise<PlayerCompletionPage> => {
  const cursor = next ? new URL(next, "http://localhost").searchParams.get("cursor") : null;
  const response = await axios.get<PlayerCompletionPage>(
    `/builder/worlds/${worldId}/players/${playerId}/completions/`,
    { params: cursor ? { cursor } : {} },
  );
  return response.data;
};

export const formatClearTime = (milliseconds: number): string => {
  const totalSeconds = Math.floor(milliseconds / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = `${totalSeconds % 60}.${String(milliseconds % 1000).padStart(3, "0")}s`;
  return [hours ? `${hours}h` : "", hours || minutes ? `${minutes}m` : "", seconds].filter(Boolean).join(" ");
};
