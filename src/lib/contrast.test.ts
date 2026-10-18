import { describe, expect, it } from "vitest";
import { DUSK } from "./palette";

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((start) => Number.parseInt(hex.slice(start, start + 2), 16) / 255);
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe.each(["light", "dark"] as const)("Dusk %s theme", (theme) => {
  const palette = DUSK[theme];

  it("keeps body text at AAA against every wash stop", () => {
    for (const stop of palette.wash) expect(contrast(palette.ink, stop)).toBeGreaterThanOrEqual(7);
  });

  it("keeps secondary and tertiary text readable on the wash", () => {
    for (const stop of palette.wash) {
      expect(contrast(palette.ink2, stop)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(palette.ink3, stop)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("keeps text on accent buttons readable", () => {
    expect(contrast(palette.onAccent, palette.accent)).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps accent text and links readable on the wash", () => {
    for (const stop of palette.wash) expect(contrast(palette.accent, stop)).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps the decorative glow visible as a focus ring (3:1)", () => {
    for (const stop of palette.wash) expect(contrast(palette.glow, stop)).toBeGreaterThanOrEqual(3);
  });
});
