import { createApp, h, nextTick, ref } from "vue";
import { afterEach, describe, expect, it } from "vitest";

import type { CollectionDocument } from "@/api/types";
import ResearchScopeDrawer from "@/features/research/ResearchScopeDrawer.vue";

const documents: CollectionDocument[] = [
  {
    document_id: "00000000-0000-4000-8000-000000000001",
    paper_id: "00000000-0000-4000-8000-000000000101",
    doi: "10.1000/first",
    title: "第一篇范围文献",
    authors: [{ given: "Ming", family: "Li" }],
    publication_year: 2024,
    venue: "研究方法学报",
    citation_text: "Li M. 第一篇范围文献[J]. 研究方法学报, 2024.",
    tags: [],
    note: null,
    original_filename: "first.pdf",
    byte_size: 1024,
    source_url: "https://example.test/first",
    access_rights: "open",
    added_at: "2026-08-07T00:00:00Z",
    latest_ingestion_run: null,
  },
  {
    document_id: "00000000-0000-4000-8000-000000000002",
    paper_id: "00000000-0000-4000-8000-000000000102",
    doi: "10.1000/second",
    title: "第二篇范围文献",
    authors: [{ literal: "研究团队" }],
    publication_year: 2025,
    venue: "证据研究",
    citation_text: "研究团队. 第二篇范围文献[J]. 证据研究, 2025.",
    tags: ["比较"],
    note: "用于核对第二组证据。",
    original_filename: "second.pdf",
    byte_size: 2048,
    source_url: null,
    access_rights: "restricted",
    added_at: "2026-08-07T00:00:00Z",
    latest_ingestion_run: null,
  },
];

const mountedApps: Array<{ unmount: () => void; host: HTMLElement }> = [];

afterEach(() => {
  for (const { unmount, host } of mountedApps.splice(0)) {
    unmount();
    host.remove();
  }
  document.querySelectorAll(".research-scope-overlay").forEach((element) => element.remove());
});

describe("ResearchScopeDrawer", () => {
  it("允许在范围内切换文献并关闭抽屉", async () => {
    const open = ref(true);
    const host = document.createElement("div");
    document.body.append(host);
    const app = createApp({
      setup() {
        return () =>
          h(ResearchScopeDrawer, {
            open: open.value,
            "onUpdate:open": (value: boolean) => {
              open.value = value;
            },
            documents,
            loading: false,
            error: false,
          });
      },
    });
    app.mount(host);
    mountedApps.push({ unmount: () => app.unmount(), host });

    expect(document.body.textContent).toContain("第一篇范围文献");
    const secondDocument = Array.from(
      document.querySelectorAll<HTMLButtonElement>(".research-scope-document-item"),
    ).find((button) => button.textContent?.includes("第二篇范围文献"));
    secondDocument?.click();
    await nextTick();

    expect(document.body.textContent).toContain("第二篇范围文献");
    expect(document.body.textContent).toContain("用于核对第二组证据。");

    document.querySelector<HTMLButtonElement>('[title="关闭研究范围"]')?.click();
    await nextTick();

    expect(open.value).toBe(false);
    expect(document.querySelector(".research-scope-overlay")).toBeNull();
  });
});
