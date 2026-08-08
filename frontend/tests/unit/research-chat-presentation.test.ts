import { describe, expect, it } from "vitest";

import type { ResearchEvidence, ResearchRun } from "@/api/types";
import {
  candidateEvidences,
  citationAuditLabel,
  citedEvidenceIndexes,
  citedEvidences,
  evidenceAuthors,
  isEvidenceInsufficientRun,
} from "@/features/research/research-chat-presentation";

function evidence(id: string, displayIndex: number | null, isCited: boolean): ResearchEvidence {
  return { id, display_index: displayIndex, is_cited: isCited } as ResearchEvidence;
}

function run(options: {
  executionMode: "fast_rag" | "strict_research";
  evidences?: ResearchEvidence[];
  status?: ResearchRun["status"];
  claimVerified?: boolean;
  citationChecked?: boolean;
}): ResearchRun {
  return {
    status: options.status ?? "completed",
    evidences: options.evidences ?? [],
    retrieval_trace: {
      execution_mode: options.executionMode,
      claim_verified: options.claimVerified,
      citation_checked: options.citationChecked,
    },
  } as unknown as ResearchRun;
}

describe("research-chat-presentation", () => {
  it("兼容证据中已保存的作者字典形状", () => {
    const currentEvidence = {
      authors: [
        { name: " Ada Lovelace " },
        { literal: "证据研究团队" },
        { given: "Ming", family: "Li" },
      ],
    } as unknown as ResearchEvidence;

    expect(evidenceAuthors(currentEvidence)).toBe("Ada Lovelace、证据研究团队、Ming Li");
  });

  it("只将实际引用的证据按展示索引作为默认来源", () => {
    const currentRun = run({
      executionMode: "fast_rag",
      evidences: [
        evidence("candidate", null, false),
        evidence("second", 2, true),
        evidence("first", 1, true),
      ],
    });

    expect(citedEvidences(currentRun).map((item) => item.id)).toEqual(["first", "second"]);
    expect(citedEvidenceIndexes(currentRun)).toEqual([1, 2]);
    expect(candidateEvidences(currentRun)).toEqual([]);
  });

  it("仅深度研究公开候选证据，并按模式限制核验文案", () => {
    const evidences = [evidence("cited", 1, true), evidence("candidate", null, false)];
    const fastRun = run({
      executionMode: "fast_rag",
      evidences,
      claimVerified: true,
      citationChecked: true,
    });
    const strictRun = run({
      executionMode: "strict_research",
      evidences,
      claimVerified: true,
      citationChecked: true,
    });

    expect(citationAuditLabel(fastRun)).toBe("1 条引用已检查");
    expect(candidateEvidences(strictRun).map((item) => item.id)).toEqual(["candidate"]);
    expect(citationAuditLabel(strictRun)).toBe("1 条引用与主张已核验");
  });

  it("将没有实际引用的澄清终态识别为证据不足", () => {
    expect(
      isEvidenceInsufficientRun(
        run({ executionMode: "fast_rag", status: "awaiting_clarification" }),
      ),
    ).toBe(true);
    expect(
      isEvidenceInsufficientRun(
        run({
          executionMode: "fast_rag",
          status: "awaiting_clarification",
          evidences: [evidence("cited", 1, true)],
        }),
      ),
    ).toBe(false);
  });
});
