// The Dusk palette. index.css mirrors these values as CSS custom properties;
// contrast.test.ts checks them so a colour tweak cannot silently hurt readability.

export interface ThemePalette {
  wash: [string, string, string];
  ink: string;
  ink2: string;
  ink3: string;
  /** Fills and accent text; deep enough for readable text on top and around it. */
  accent: string;
  onAccent: string;
  /** The brighter mockup coral, for the mark, glows and focus rings only. */
  glow: string;
  danger: string;
}

export const DUSK: Record<"light" | "dark", ThemePalette> = {
  light: {
    wash: ["#FCEFE3", "#F4F1F6", "#EEF2F8"],
    ink: "#1C1B2A",
    ink2: "#4B4A5E",
    ink3: "#686679",
    accent: "#C2401B",
    onAccent: "#FFFFFF",
    glow: "#E4572E",
    danger: "#B3261E",
  },
  dark: {
    wash: ["#2A1B2E", "#151628", "#0D1020"],
    ink: "#F6EEF0",
    ink2: "#C7BFCB",
    ink3: "#9A93A6",
    accent: "#FF7B54",
    onAccent: "#1A0E14",
    glow: "#FF7B54",
    danger: "#FF8A80",
  },
};
