import type { Channel } from "../api/types";

export type Category = {
  id: string;
  title: string;
  count: number;
};

const LABELS: Record<string, string> = {
  general: "General",
  news: "Noticias",
  sports: "Deportes",
};

const titleCase = (value: string) =>
  value
    .replace(/[-_]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (char) => char.toUpperCase());

export const buildCategories = (channels: Channel[]): Category[] => {
  const counts = new Map<string, number>();
  channels.forEach((channel) => {
    const id = channel.category?.trim() || "general";
    counts.set(id, (counts.get(id) ?? 0) + 1);
  });

  const categories = Array.from(counts.entries())
    .map(([id, count]) => ({
      id,
      title: LABELS[id] ?? titleCase(id),
      count,
    }))
    .sort((a, b) => a.title.localeCompare(b.title));

  return [{ id: "", title: "Todos", count: channels.length }, ...categories];
};

export const channelsForCategory = (channels: Channel[], categoryId: string) => {
  if (!categoryId) return channels;
  return channels.filter((channel) => (channel.category?.trim() || "general") === categoryId);
};
