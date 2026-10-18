// Google account connection (Gmail and Calendar) through the sidecar's OAuth flow.

import { postJson, sidecarJson } from "../lib/sidecar";

export interface GoogleStatus {
  connected: boolean;
  configured: boolean;
  email: string | null;
  error: string | null;
}

export function getGoogleStatus(): Promise<GoogleStatus> {
  return sidecarJson<GoogleStatus>("/google/auth/status");
}

/** Save client credentials and return the Google consent URL to open. */
export async function startGoogleSignIn(clientId: string, clientSecret: string): Promise<string> {
  const configured = await postJson<{ success: boolean; error?: string }>("/google/auth/configure", {
    client_id: clientId,
    client_secret: clientSecret,
  });
  if (!configured.success) throw new Error(configured.error ?? "Could not save the Google client details.");
  const url = await sidecarJson<{ success: boolean; auth_url?: string; error?: string }>("/google/auth/url");
  if (!url.success || !url.auth_url) throw new Error(url.error ?? "Could not start Google sign-in.");
  return url.auth_url;
}

export async function disconnectGoogle(): Promise<void> {
  const result = await postJson<{ success: boolean; error?: string }>("/google/auth/disconnect");
  if (!result.success) throw new Error(result.error ?? "Could not disconnect the Google account.");
}
