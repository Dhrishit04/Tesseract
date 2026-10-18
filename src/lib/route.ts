// Hash routes: #/chat/<id>, #/chat/new, #/settings/<section>, #/settings/about/<terms|privacy>.

export const SETTINGS_SECTIONS = ["profile", "appearance", "models", "google", "voice", "automation", "startup", "about"] as const;
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];
export type LegalKind = "terms" | "privacy";

export type Route =
  | { view: "home" }
  | { view: "chat"; chatId: string | null }
  | { view: "settings"; section: SettingsSection; legal: LegalKind | null };

function isSection(value: string | undefined): value is SettingsSection {
  return SETTINGS_SECTIONS.includes(value as SettingsSection);
}

export function parseRoute(hash: string): Route {
  const [first, second, third] = hash.replace(/^#\/?/, "").split("/");
  switch (first) {
    case "chat":
      return { view: "chat", chatId: second && second !== "new" ? decodeURIComponent(second) : null };
    case "settings": {
      const section = isSection(second) ? second : "profile";
      const legal = section === "about" && (third === "terms" || third === "privacy") ? third : null;
      return { view: "settings", section, legal };
    }
    case "terms":
    case "privacy":
      return { view: "settings", section: "about", legal: first };
    case "overview":
      return { view: "settings", section: "about", legal: null };
    default:
      return { view: "home" };
  }
}
