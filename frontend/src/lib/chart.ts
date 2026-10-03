// Validated categorical palette (fixed order, light/dark steps) - colors follow the entity, never its rank.
const LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];
const DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];

export const isDark = () => document.documentElement.classList.contains("dark");
export const series = (i: number) => (isDark() ? DARK : LIGHT)[i % 8];
export const brand = () => (isDark() ? "#b78cff" : "#8b3fe6");
export const grid = () => (isDark() ? "rgba(255,255,255,.07)" : "rgba(31,21,48,.07)");
export const axis = () => (isDark() ? "#a49cb8" : "#6b6380");

/** Stable color per entity name based on a reference ordering (e.g. all service lines). */
export function colorMap(names: string[]): (name: string) => string {
  const idx = new Map(names.map((n, i) => [n, i]));
  return (name) => series(idx.get(name) ?? Math.min(names.length, 7));
}

export const tooltipStyle = () => ({
  contentStyle: {
    background: isDark() ? "#1a1427" : "#ffffff",
    border: `1px solid ${isDark() ? "rgba(192,132,252,.2)" : "rgba(124,58,237,.12)"}`,
    borderRadius: 14,
    fontFamily: "Vazirmatn",
    fontSize: 12,
    direction: "rtl" as const,
    color: isDark() ? "#f1ecfa" : "#1f1530",
  },
  labelStyle: { color: isDark() ? "#a49cb8" : "#6b6380", marginBottom: 4 },
  itemStyle: { color: isDark() ? "#f1ecfa" : "#1f1530" },
});
