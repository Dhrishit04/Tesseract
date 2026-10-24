// The actions the owner chose "Always allow this" for.

import { sidecarJson } from "../lib/sidecar";

export interface TrustedTool {
  tool: string;
  title: string;
}

export async function listTrusted(): Promise<TrustedTool[]> {
  return (await sidecarJson<{ trusted: TrustedTool[] }>("/permissions/trusted")).trusted;
}

/** Ask before this action runs again. */
export async function revokeTrusted(tool: string): Promise<void> {
  await sidecarJson(`/permissions/trusted/${encodeURIComponent(tool)}`, { method: "DELETE" });
}
