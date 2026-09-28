export interface QuestCardData {
  key: string;
  title: string;
  slug: string;
  badges: { label: string; tone: string }[];
  body: string;
  recapLines: string[];
  objectives: { id: string; text: string; progress: string; status: string }[];
  choiceRows: { id: string; text: string; command: string }[];
  metaLines: string[];
  rewardLines: string[];
  actions: { label: string; command: string; tone: string }[];
}

export interface QuestCardSource {
  id?: number;
  status?: string;
  template?: { name?: string; slug?: string; quest_type?: string };
  current_step?: {
    text?: { body?: string };
    recap?: string;
    objectives?: {
      id?: string; text?: string; status?: string;
      progress_current?: number; progress_target?: number;
    }[];
    choices?: { id: string; text: string }[];
  };
}

export const splitQuestLines = (value: unknown): string[] => String(value || "")
  .split("\n").map((line) => line.trim()).filter(Boolean);

export const buildQuestBadges = (questType?: string, status?: string) => {
  const badges: QuestCardData["badges"] = [];
  if (questType && questType !== "quest") badges.push({ label: questType, tone: "tone-type" });
  if (status === "active") badges.push({ label: "active", tone: "tone-active" });
  if (status === "resolved") badges.push({ label: "resolved", tone: "tone-resolved" });
  return badges;
};

export const questInstanceCard = (quest: QuestCardSource): QuestCardData => {
  const template = quest.template || {};
  const step = quest.current_step || {};
  const slug = template.slug || "";
  return {
    key: `quest-${quest.id || slug || "current"}`,
    title: template.name || slug || "Quest",
    slug,
    badges: buildQuestBadges(template.quest_type, quest.status),
    body: String(step.text?.body || "").trim(),
    recapLines: splitQuestLines(step.recap),
    objectives: (step.objectives || [])
      .filter((objective) => objective && objective.status !== "hidden")
      .map((objective) => {
        const current = Number(objective.progress_current || 0);
        const target = Number(objective.progress_target || 0);
        return {
          id: objective.id || objective.text || "objective",
          text: objective.text || objective.id || "Objective",
          progress: target > 0 ? `${current}/${target}` : `${current}`,
          status: objective.status || "active",
        };
      }),
    choiceRows: (step.choices || []).map((choice) => ({
      id: choice.id,
      text: choice.text || choice.id,
      command: slug ? `quest choose ${slug} ${choice.id}` : "",
    })),
    metaLines: [],
    rewardLines: [],
    actions: [],
  };
};
