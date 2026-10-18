import { describe, expect, it } from "vitest";
import { parseRoute } from "./route";

describe("parseRoute", () => {
  it("reads chats", () => {
    expect(parseRoute("#/chat/abc123")).toEqual({ view: "chat", chatId: "abc123" });
    expect(parseRoute("#/chat/new")).toEqual({ view: "chat", chatId: null });
  });

  it("reads settings sections and legal pages under About", () => {
    expect(parseRoute("#/settings")).toEqual({ view: "settings", section: "profile", legal: null });
    expect(parseRoute("#/settings/voice")).toEqual({ view: "settings", section: "voice", legal: null });
    expect(parseRoute("#/settings/about/privacy")).toEqual({ view: "settings", section: "about", legal: "privacy" });
  });

  it("maps legacy and unknown routes", () => {
    expect(parseRoute("")).toEqual({ view: "home" });
    expect(parseRoute("#/workspace")).toEqual({ view: "home" });
    expect(parseRoute("#/terms")).toEqual({ view: "settings", section: "about", legal: "terms" });
    expect(parseRoute("#/overview")).toEqual({ view: "settings", section: "about", legal: null });
    expect(parseRoute("#/settings/nonsense")).toEqual({ view: "settings", section: "profile", legal: null });
  });
});
