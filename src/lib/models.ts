// Model refs are "chatgpt:<slug>" or "anthropic:<id>"; OpenRouter ids are bare and may contain ":".

import type { ModelInfo, ModelList } from "../services/api";

export const PROVIDER_LABELS: Record<string, string> = {
  chatgpt: "ChatGPT",
  anthropic: "Claude (Anthropic)",
  openrouter: "OpenRouter",
};

export function providerOf(ref: string): string {
  const prefix = ref.split(":", 1)[0];
  return ref.includes(":") && prefix in PROVIDER_LABELS ? prefix : "openrouter";
}

export function groupModels(models: ModelInfo[]) {
  return Object.entries(PROVIDER_LABELS)
    .map(([provider, label]) => ({ provider, label, models: models.filter((model) => model.provider === provider) }))
    .filter((group) => group.models.length);
}

/** The current model if it is listed, else the first listed one. A list missing a failed provider proves nothing, so it changes nothing. */
export function pickAvailable(current: string, { models, errors }: ModelList): string {
  if (!models.length || Object.keys(errors).length || models.some((model) => model.id === current)) return current;
  return models[0].id;
}
