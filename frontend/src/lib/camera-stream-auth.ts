import { CanceledError } from "axios";
import { api } from "@/lib/api";

// The nginx/go2rtc access cookie lives for 12 hours. Renew it before expiry,
// while keeping one request shared by every camera tile.
const TOKEN_RENEW_AFTER_MS = 10 * 60 * 60 * 1000;
const AUTH_RENEW_COOLDOWN_MS = 60 * 1000;

type TokenFlight = {
  controller: AbortController;
  generation: number;
  promise: Promise<void>;
};

let generation = 0;
let tokenIssuedAt = 0;
let tokenRequest: TokenFlight | null = null;
let lastForcedTokenRenewal = 0;

/**
 * Forget all camera authorization state for the current browser session.
 * Aborting is best-effort; the generation check also rejects a late response
 * from adapters that resolve after AbortController.abort().
 */
export function invalidateCameraStreamToken() {
  generation += 1;
  tokenIssuedAt = 0;
  lastForcedTokenRenewal = 0;
  tokenRequest?.controller.abort();
  tokenRequest = null;
}

/** One shared token request for every camera tile on the page. */
export function ensureCameraStreamToken(force = false): Promise<void> {
  const now = Date.now();
  if (tokenRequest) return tokenRequest.promise;
  if (!force && tokenIssuedAt && now - tokenIssuedAt < TOKEN_RENEW_AFTER_MS) {
    return Promise.resolve();
  }
  if (force && lastForcedTokenRenewal && now - lastForcedTokenRenewal < AUTH_RENEW_COOLDOWN_MS) {
    return Promise.resolve();
  }
  if (force) lastForcedTokenRenewal = now;

  const requestGeneration = generation;
  const controller = new AbortController();
  const promise = api
    .post("/cameras/token/", undefined, { timeout: 10_000, signal: controller.signal })
    .then(() => {
      if (requestGeneration !== generation) {
        throw new CanceledError("Camera authorization session changed");
      }
      tokenIssuedAt = Date.now();
    })
    .finally(() => {
      if (tokenRequest?.promise === promise) tokenRequest = null;
    });
  const flight = { controller, generation: requestGeneration, promise } satisfies TokenFlight;
  tokenRequest = flight;
  return promise;
}
