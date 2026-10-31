import { afterEach, describe, expect, it, vi } from "vitest";
import { listModels } from "../services/api";
import { groupModels, pickAvailable, providerOf } from "./models";

describe("pickAvailable", () => {
  const models = [{ id: "chatgpt:g", provider: "chatgpt" }, { id: "anthropic:c", provider: "anthropic" }];
  const loaded = { models, errors: {} };

  it("keeps a listed model", () => {
    expect(pickAvailable("anthropic:c", loaded)).toBe("anthropic:c");
  });

  it("swaps an unlisted model for the first listed one", () => {
    expect(pickAvailable("anthropic/claude-3.5-sonnet", loaded)).toBe("chatgpt:g");
  });

  it("leaves the model alone when nothing is listed", () => {
    expect(pickAvailable("anthropic/claude-3.5-sonnet", { models: [], errors: {} })).toBe("anthropic/claude-3.5-sonnet");
  });

  it("leaves the model alone when a provider failed to load", () => {
    expect(pickAvailable("openrouter/x", { models, errors: { openrouter: "Could not load." } })).toBe("openrouter/x");
  });
});

describe("listModels", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("returns the models and each provider's error", async () => {
    const body = { models: [{ id: "x/y", provider: "openrouter" }], errors: { anthropic: "Could not load Anthropic models." } };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(body))));
    await expect(listModels()).resolves.toEqual(body);
  });

  it("fills in missing fields", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}")));
    await expect(listModels()).resolves.toEqual({ models: [], errors: {} });
  });
});

describe("groupModels", () => {
  it("orders groups chatgpt, anthropic, openrouter and keeps server order inside each", () => {
    const groups = groupModels([
      { id: "x/b", name: "B", provider: "openrouter" },
      { id: "anthropic:z", name: "Z", provider: "anthropic" },
      { id: "x/a", name: "A", provider: "openrouter" },
      { id: "chatgpt:g", name: "G", provider: "chatgpt" },
    ]);
    expect(groups.map((group) => group.provider)).toEqual(["chatgpt", "anthropic", "openrouter"]);
    expect(groups[2].models.map((model) => model.id)).toEqual(["x/b", "x/a"]);
    expect(groups[1].label).toBe("Claude (Anthropic)");
  });

  it("drops empty groups", () => {
    expect(groupModels([{ id: "chatgpt:g", provider: "chatgpt" }]).map((group) => group.provider)).toEqual(["chatgpt"]);
  });
});

describe("providerOf", () => {
  it("splits on a known prefix only", () => {
    expect(providerOf("anthropic:x")).toBe("anthropic");
    expect(providerOf("a/b:free")).toBe("openrouter");
    expect(providerOf("chatgpt:gpt-6.1-sol")).toBe("chatgpt");
    expect(providerOf("openrouter:q")).toBe("openrouter");
  });
});
