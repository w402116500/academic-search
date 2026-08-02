import { describe, expect, it } from "vitest";

import { buildResearchScope } from "@/features/research/scope";

describe("buildResearchScope", () => {
  it("将近五年预设转换为以当前年结尾的范围", () => {
    expect(
      buildResearchScope({
        timePreset: "last5",
        startYear: null,
        endYear: null,
        languages: ["zh", "en"],
        currentYear: 2026,
      }),
    ).toEqual({ start_year: 2022, end_year: 2026, languages: ["zh", "en"] });
  });

  it("拒绝晚于当前年的自定义结束年份", () => {
    expect(() =>
      buildResearchScope({
        timePreset: "custom",
        startYear: 2023,
        endYear: 2027,
        languages: ["en"],
        currentYear: 2026,
      }),
    ).toThrow("自定义年份必须在 1900 至 2026 年之间");
  });

  it("要求用户至少保留一种文献语言", () => {
    expect(() =>
      buildResearchScope({
        timePreset: "any",
        startYear: null,
        endYear: null,
        languages: [],
        currentYear: 2026,
      }),
    ).toThrow("至少选择一种文献语言。");
  });
});
