// Choices offered in Settings.

export const option = (value: string, label = value) => ({ value, label });

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
export const FILE_ACCESS_LEVELS = [
  { ...option("full", "Entire device"), hint: "Every file and drive your Windows or macOS account can open." },
  { ...option("profile", "Your user folders"), hint: "Your whole user profile, plus Desktop, Documents and Downloads wherever they are stored." },
  { ...option("personal", "Desktop, Documents and Downloads"), hint: "Only these three folders, wherever they are stored." },
  { ...option("workspace", "Working folder only"), hint: "Only the folder Tesseract runs in." },
];
export const CLOUD_VOICES = ["alloy", "echo", "fable", "onyx", "nova", "shimmer"].map((voice) =>
  option(voice, voice[0].toUpperCase() + voice.slice(1)),
);
