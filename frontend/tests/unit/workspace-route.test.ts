import { describe, expect, it } from "vitest";

import { workspaceRouteForStage } from "@/router/workspace-route";

describe("workspaceRouteForStage", () => {
  it("将每个服务端阶段送到可继续操作的对应页面", () => {
    expect(workspaceRouteForStage("draft")).toBe("workspace-runner");
    expect(workspaceRouteForStage("analyzing")).toBe("workspace-runner");
    expect(workspaceRouteForStage("plan_review")).toBe("workspace-runner");
    expect(workspaceRouteForStage("retrieving")).toBe("workspace-runner");
    expect(workspaceRouteForStage("screening")).toBe("workspace-results");
    expect(workspaceRouteForStage("collection_building")).toBe("workspace-collection");
    expect(workspaceRouteForStage("researching")).toBe("workspace-collection");
    expect(workspaceRouteForStage("failed")).toBe("workspace-runner");
  });
});
