import type { WorkflowStage } from "@/api/types";

export type WorkspaceRouteName = "workspace-runner" | "workspace-results" | "workspace-collection";

/**
 * 将后端工作流阶段映射为用户可以继续操作的页面。
 * 阶段值由后端维护，前端只负责选择对应入口，不复制状态机逻辑。
 */
export function workspaceRouteForStage(stage: WorkflowStage): WorkspaceRouteName {
  switch (stage) {
    case "retrieving":
      return "workspace-runner";
    case "screening":
      return "workspace-results";
    case "collection_building":
    case "researching":
      return "workspace-collection";
    case "draft":
    case "analyzing":
    case "plan_review":
    case "failed":
    default:
      return "workspace-runner";
  }
}
