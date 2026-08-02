import type { ResearchScope } from "@/api/types";

export type ResearchTimePreset = "any" | "last3" | "last5" | "custom";

export interface ResearchScopeInput {
  timePreset: ResearchTimePreset;
  startYear: number | null;
  endYear: number | null;
  languages: Array<"zh" | "en">;
  currentYear: number;
}

/**
 * 将页面的时间与语言选择转换为后端确认计划所需的稳定范围对象。
 * 当前年份由调用方注入，使边界规则可在测试中稳定验证。
 */
export function buildResearchScope(input: ResearchScopeInput): ResearchScope {
  let startYear: number | null = null;
  let endYear: number | null = null;

  if (input.timePreset === "last3") {
    startYear = input.currentYear - 2;
    endYear = input.currentYear;
  }
  if (input.timePreset === "last5") {
    startYear = input.currentYear - 4;
    endYear = input.currentYear;
  }
  if (input.timePreset === "custom") {
    if (
      !input.startYear ||
      !input.endYear ||
      input.startYear > input.endYear ||
      input.endYear > input.currentYear ||
      input.startYear < 1900
    ) {
      throw new Error(
        `自定义年份必须在 1900 至 ${input.currentYear} 年之间，且起始年份不能晚于结束年份。`,
      );
    }
    startYear = input.startYear;
    endYear = input.endYear;
  }
  if (!input.languages.length) throw new Error("至少选择一种文献语言。");

  return { start_year: startYear, end_year: endYear, languages: input.languages };
}
