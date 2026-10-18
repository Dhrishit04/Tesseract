import { useEffect, useState } from "react";
import { ModelInfo, listModels } from "../services/api";

interface ModelPickerProps {
  currentModel: string;
  onModelChange: (model: string) => void;
}

// Shown until an API key lets the sidecar fetch OpenRouter's full list.
const STARTER_MODELS: ModelInfo[] = [
  { id: "anthropic/claude-3.5-sonnet", name: "Claude 3.5 Sonnet" },
  { id: "openai/gpt-4o", name: "GPT-4o" },
  { id: "google/gemini-pro-1.5", name: "Gemini Pro 1.5" },
  { id: "meta-llama/llama-3.1-70b-instruct", name: "Llama 3.1 70B" },
];

export function ModelPicker({ currentModel, onModelChange }: Readonly<ModelPickerProps>) {
  const [models, setModels] = useState<ModelInfo[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    listModels()
      .then((result) => !cancelled && setModels(result.length ? result : STARTER_MODELS))
      .catch(() => !cancelled && setModels(STARTER_MODELS));
    return () => {
      cancelled = true;
    };
  }, []);

  if (!models) {
    return <span className="skeleton skeleton-select" aria-label="Loading models" role="status" />;
  }

  const options = models.some((model) => model.id === currentModel)
    ? models
    : [{ id: currentModel }, ...models];

  return (
    <div className="model-picker">
      <label htmlFor="model-picker" className="visually-hidden">Model</label>
      <select id="model-picker" name="model" value={currentModel} onChange={(event) => onModelChange(event.target.value)}>
        {options.map((model) => (
          <option key={model.id} value={model.id}>
            {model.name ?? model.id}
          </option>
        ))}
      </select>
    </div>
  );
}
