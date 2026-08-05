import type { CandidateLanguage } from "@/api/types";

/**
 * 为旧检索会话兼容语言字段缺失的情况。
 *
 * 候选会暂存在 Redis，升级后仍可能读取到没有 ``language`` 的历史快照；此时界面
 * 必须诚实显示“待识别”，而不是把它默认渲染为英文文献。
 */
export function normalizeCandidateLanguage(
  language: CandidateLanguage | null | undefined,
): CandidateLanguage {
  return language ?? "unknown";
}

/** 将机器可筛选的语言码转换成用户能一眼理解的中文标签。 */
export function candidateLanguageLabel(language: CandidateLanguage | null | undefined): string {
  const labels: Record<CandidateLanguage, string> = {
    zh: "中文文献",
    en: "英文文献",
    other: "其他语种",
    unknown: "待识别语种",
  };
  return labels[normalizeCandidateLanguage(language)];
}
