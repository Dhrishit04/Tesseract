// Shared access to the local Python sidecar and the Tauri runtime.

export const SIDECAR_URL = import.meta.env.VITE_SIDECAR_URL ?? "http://127.0.0.1:8765";

/** True inside the desktop app; false when the UI runs in a plain browser (vite dev). */
export function isTauri(): boolean {
  return "__TAURI_INTERNALS__" in window;
}

export class SidecarError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = "SidecarError";
  }
}

/** JSON request to the sidecar. Network failures become a readable SidecarError. */
export async function sidecarJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${SIDECAR_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new SidecarError("The assistant service is not running.");
  }
  if (!response.ok) {
    const detail = await response.json().then((body) => body?.detail, () => undefined);
    throw new SidecarError(
      typeof detail === "string" ? detail : `The assistant service returned ${response.status}.`,
      response.status,
    );
  }
  return (await response.json()) as T;
}

export function postJson<T>(path: string, body?: unknown): Promise<T> {
  return sidecarJson<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
}

/** Open a URL in the user's default browser (never inside the app webview). */
export async function openExternal(url: string): Promise<void> {
  if (isTauri()) {
    const { open } = await import("@tauri-apps/plugin-shell");
    await open(url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
