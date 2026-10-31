import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
const postJson = vi.fn();

vi.mock("@tauri-apps/api/core", () => ({ invoke: (...args: unknown[]) => invoke(...args) }));
vi.mock("../lib/sidecar", () => ({
  isTauri: () => true,
  postJson: (...args: unknown[]) => postJson(...args),
  sidecarJson: vi.fn(),
}));

const { DEFAULT_SETTINGS, getSettings, saveSettings } = await import("./settings");

describe("saveSettings in the desktop app", () => {
  beforeEach(() => {
    invoke.mockReset().mockResolvedValue(undefined);
    postJson.mockReset();
  });

  it("counts as saved when the file was written but the assistant could not reload", async () => {
    postJson.mockRejectedValue(new Error("The assistant service is not running."));

    await expect(saveSettings(DEFAULT_SETTINGS)).resolves.toEqual({ applied: false });
    expect(invoke).toHaveBeenCalledWith("save_settings", { settings: DEFAULT_SETTINGS });
  });

  it("reports applied when the assistant reloaded", async () => {
    postJson.mockResolvedValue({ success: true });

    await expect(saveSettings(DEFAULT_SETTINGS)).resolves.toEqual({ applied: true });
  });

  it("still fails when the file itself could not be written", async () => {
    invoke.mockRejectedValue(new Error("disk full"));

    await expect(saveSettings(DEFAULT_SETTINGS)).rejects.toThrow("disk full");
  });
});

describe("getSettings", () => {
  it("drops secrets that now live in the vault", async () => {
    invoke.mockReset().mockResolvedValue({ openrouter_api_key: "sk-or-x", google: { client_id: "i", client_secret: "s" } });

    const settings = await getSettings();

    expect(settings).not.toHaveProperty("openrouter_api_key");
    expect(settings.google).not.toHaveProperty("client_secret");
    expect(settings.google.client_id).toBe("i");
  });
});
