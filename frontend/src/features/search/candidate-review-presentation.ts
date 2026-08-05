import { citationReadinessMessage, isFulltextTerminal } from "./search-run-state";
import type { Candidate, FulltextResponse } from "@/api/types";

export function candidateProcessingSummary(
  candidate: Candidate,
  fulltext: FulltextResponse | null,
): string {
  if (!candidate.doi) return "该记录缺少 DOI，不能进入后续研究集合。";
  if (fulltext?.status === "rejected") {
    return fulltext.error?.message || "该文献不满足全文准入条件，不能进入研究集合。";
  }
  if (fulltext?.status === "failed") {
    return fulltext.error?.message || "全文获取失败，可根据提示重试或改选其他文献。";
  }
  if (fulltext?.status === "requires_upload") {
    return (
      fulltext.error?.message || "没有可处理的开放获取 PDF。请在完整记录中确认有权处理后上传文件。"
    );
  }
  if (fulltext?.status === "available") {
    return "DOI、正式题录与可处理全文均已核验，可以加入待确认研究集合。";
  }
  if (fulltext && !isFulltextTerminal(fulltext.status)) {
    return "题录与全文核验正在进行，结果会自动更新到本页。";
  }
  return candidate.citation?.status === "ready"
    ? "题录已通过核验。下一步需要获取并验证可处理的全文。"
    : `${citationReadinessMessage(candidate.citation)} 你可以开始核验，系统会先按 DOI 重新补齐题录。`;
}
