import type { ResearchPlanScope, ResearchScope } from "@/api/types";
import type { ResearchTimePreset } from "./scope";

export function providerNamesForDirection(
  byDirection: unknown,
  selectedDirectionId: string,
): string[] {
  if (!byDirection || typeof byDirection !== "object" || !selectedDirectionId) return [];
  const queries = (byDirection as Record<string, unknown>)[selectedDirectionId];
  if (!Array.isArray(queries)) return [];
  const labels: Record<string, string> = {
    openalex: "OpenAlex",
    crossref: "Crossref",
    arxiv: "arXiv",
    semantic_scholar: "Semantic Scholar",
  };
  return [
    ...new Set(
      queries.flatMap((query) => {
        if (!query || typeof query !== "object" || !("provider" in query)) return [];
        const provider = query.provider;
        return typeof provider === "string" ? [labels[provider] ?? provider] : [];
      }),
    ),
  ];
}

export function researchTimeScopeLabel(
  preset: ResearchTimePreset,
  startYear: number | null,
  endYear: number | null,
): string {
  if (preset === "last3") return "近 3 年";
  if (preset === "last5") return "近 5 年";
  if (preset === "custom" && startYear && endYear) return `${startYear} 至 ${endYear}`;
  return "不限时间";
}

export function researchLanguageLabel(languages: Array<"zh" | "en">): string {
  const labels = { zh: "中文", en: "英文" } as const;
  return languages.map((language) => labels[language]).join("、") || "未选择";
}

export function resolvePlanScope(scope: ResearchPlanScope): ResearchScope | null {
  if ("languages" in scope && Array.isArray(scope.languages)) return scope as ResearchScope;
  return scope.confirmed ?? scope.suggested ?? null;
}

export function resolveTimePreset(scope: ResearchScope, currentYear: number): ResearchTimePreset {
  if (scope.start_year === null || scope.end_year === null) return "any";
  if (scope.start_year === currentYear - 2 && scope.end_year === currentYear) return "last3";
  if (scope.start_year === currentYear - 4 && scope.end_year === currentYear) return "last5";
  return "custom";
}
