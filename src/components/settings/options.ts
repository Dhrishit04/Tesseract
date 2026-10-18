// Choices offered in Settings.

export const option = (value: string, label = value) => ({ value, label });

export const MODELS = [
  option("anthropic/claude-3.5-sonnet", "Claude 3.5 Sonnet"),
  option("anthropic/claude-3-opus", "Claude 3 Opus"),
  option("openai/gpt-4o", "GPT-4o"),
  option("openai/gpt-4-turbo", "GPT-4 Turbo"),
  option("google/gemini-pro-1.5", "Gemini Pro 1.5"),
  option("meta-llama/llama-3.1-70b-instruct", "Llama 3.1 70B"),
];
export const WAKE_WORDS = ["jarvis", "alexa", "hey google", "hey siri", "computer", "bumblebee", "picovoice", "porcupine", "terminator"]
  .map((word) => option(word, word.replace(/\b\w/g, (letter) => letter.toUpperCase())));
export const STT_MODELS = [
  option("tiny", "Tiny, about 39 MB, fastest"),
  option("base", "Base, about 74 MB"),
  option("small", "Small, about 244 MB"),
  option("medium", "Medium, about 769 MB"),
  option("large-v3", "Large v3, about 1.5 GB, most accurate"),
];
export const LANGUAGES = [
  option("en", "English"), option("es", "Spanish"), option("fr", "French"), option("de", "German"),
  option("it", "Italian"), option("pt", "Portuguese"), option("ru", "Russian"), option("ja", "Japanese"),
  option("ko", "Korean"), option("zh", "Chinese"), option("auto", "Detect automatically"),
];
export const TTS_ENGINES = [option("piper", "Piper, runs on this computer"), option("openrouter", "OpenRouter, cloud voice")];
export const PIPER_VOICES = [
  option("en_US-lessac-medium", "Lessac, US English, male"),
  option("en_US-amy-low", "Amy, US English, female"),
  option("en_US-ryan-high", "Ryan, US English, male"),
  option("en_US-kristin-medium", "Kristin, US English, female"),
  option("en_GB-alan-low", "Alan, British English, male"),
  option("en_GB-semaine-medium", "Semaine, British English, female"),
];
export const CLOUD_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"].map((voice) =>
  option(voice, voice[0].toUpperCase() + voice.slice(1)),
);
