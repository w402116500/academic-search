import { createApp, h, nextTick } from "vue";
import { afterEach, describe, expect, it } from "vitest";

import ResearchAnswerMarkdown from "@/features/research/ResearchAnswerMarkdown.vue";
import { parseAnswerMarkdown } from "@/features/research/answer-markdown";

const mountedApps: Array<{ unmount: () => void; host: HTMLElement }> = [];

afterEach(() => {
  for (const { unmount, host } of mountedApps.splice(0)) {
    unmount();
    host.remove();
  }
});

describe("parseAnswerMarkdown", () => {
  it("保留标题、列表、引用块、表格和受控引用索引", () => {
    const blocks = parseAnswerMarkdown(
      [
        "## 研究结论",
        "",
        "结论来自 **原文证据 [1]**。",
        "",
        "- 第一项",
        "- 第二项 [2]",
        "",
        "> 仅以当前集合为准。",
        "",
        "| 维度 | 结果 |",
        "| --- | --- |",
        "| 方法 | 支持 [1] |",
      ].join("\n"),
      [1, 2],
    );

    expect(blocks.map((block) => block.kind)).toEqual([
      "heading",
      "paragraph",
      "list",
      "blockquote",
      "table",
    ]);
    expect(blocks[0]).toMatchObject({ kind: "heading", level: 2 });
    expect(JSON.stringify(blocks)).toContain('"citation","index":1');
    expect(JSON.stringify(blocks)).toContain('"citation","index":2');
  });

  it("把原始 HTML、脚本和图片保留为文本而非 DOM", async () => {
    let inspectedCitation: number | null = null;
    const host = document.createElement("div");
    document.body.append(host);
    const app = createApp({
      render: () =>
        h(ResearchAnswerMarkdown, {
          content:
            "<script>window.untrusted = true</script>\n\n![图片](https://example.test/image.png) [1]",
          citationIndexes: [1],
          onInspectCitation: (index: number) => {
            inspectedCitation = index;
          },
        }),
    });
    app.mount(host);
    mountedApps.push({ unmount: () => app.unmount(), host });
    await nextTick();

    expect(host.querySelector("script")).toBeNull();
    expect(host.querySelector("img")).toBeNull();
    expect(host.textContent).toContain("<script>window.untrusted = true</script>");

    host.querySelector<HTMLButtonElement>(".research-answer-citation")?.click();
    expect(inspectedCitation).toBe(1);
  });
});
