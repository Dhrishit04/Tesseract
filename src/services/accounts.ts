// Model accounts: ChatGPT sign-in and API keys kept in the sidecar's vault.

import { postJson, sidecarJson } from "../lib/sidecar";

export interface KeyAccount {
  connected: boolean;
  key_hint: string | null;
}

export interface ChatGPTAccountStatus {
  connected: boolean;
  email: string | null;
  plan_enabled: boolean;
  error: string | null;
  /** True once an account has signed in on this computer before. */
  registered: boolean;
}

export interface Accounts {
  chatgpt: ChatGPTAccountStatus;
  anthropic: KeyAccount;
  openrouter: KeyAccount;
}

export type KeyProvider = "anthropic" | "openrouter";

export function getAccounts(): Promise<Accounts> {
  return sidecarJson<Accounts>("/accounts");
}

export function saveApiKey(provider: KeyProvider, apiKey: string): Promise<KeyAccount> {
  return sidecarJson<KeyAccount>(`/accounts/${provider}/key`, { method: "PUT", body: JSON.stringify({ api_key: apiKey }) });
}

export function removeApiKey(provider: KeyProvider): Promise<KeyAccount> {
  return sidecarJson<KeyAccount>(`/accounts/${provider}/key`, { method: "DELETE" });
}

/** Returns the ChatGPT sign-in URL to open. */
export async function startChatGPTSignIn(newAccount = false): Promise<string> {
  return (await postJson<{ auth_url: string }>("/accounts/chatgpt/sign-in", newAccount ? { new_account: true } : undefined)).auth_url;
}

export function signOutChatGPT(): Promise<{ revoked: boolean }> {
  return postJson<{ revoked: boolean }>("/accounts/chatgpt/sign-out");
}
