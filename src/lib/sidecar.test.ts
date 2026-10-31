import { afterEach, describe, expect, it, vi } from "vitest";
import { sidecarJson } from "./sidecar";

function mockFetch(status: number, body: string) {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status })));
}

describe("sidecarJson errors", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses the string detail and keeps the status", async () => {
    mockFetch(422, JSON.stringify({ detail: "That key looks wrong." }));
    await expect(sidecarJson("/x")).rejects.toMatchObject({ message: "That key looks wrong.", status: 422 });
  });

  it("falls back to the status message without a string detail", async () => {
    mockFetch(500, "oops");
    await expect(sidecarJson("/x")).rejects.toMatchObject({ message: "The assistant service returned 500.", status: 500 });
    mockFetch(422, JSON.stringify({ detail: [{ msg: "bad" }] }));
    await expect(sidecarJson("/x")).rejects.toMatchObject({ message: "The assistant service returned 422.", status: 422 });
  });
});
