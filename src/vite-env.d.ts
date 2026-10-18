/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Overrides the sidecar address at build time (the E2E test runs its own sidecar on another port). */
  readonly VITE_SIDECAR_URL?: string;
}
