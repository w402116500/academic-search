import type { Workspace } from "@/api/types";

export const workspaceDeletionIncompleteMessage = "工作区删除尚未完成，请稍后继续删除。";

export function isWorkspaceDeletionPending(workspace: Workspace): boolean {
  return workspace.status === "deleting";
}
