import { describe, expect, it } from "vitest";

import {
  candidateLanguageLabel,
  normalizeCandidateLanguage,
} from "@/features/research/candidate-language";

describe("candidate language", () => {
  it("将语言码显示为可读的候选筛选标签", () => {
    expect(candidateLanguageLabel("zh")).toBe("中文文献");
    expect(candidateLanguageLabel("en")).toBe("英文文献");
  });

  it("兼容升级前没有语言字段的 Redis 候选快照", () => {
    expect(normalizeCandidateLanguage(undefined)).toBe("unknown");
    expect(candidateLanguageLabel(undefined)).toBe("待识别语种");
  });
});
