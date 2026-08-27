import type { ErrorEvent, Log, init as initializeSentry } from "@sentry/nextjs";

type SentryOptions = Parameters<typeof initializeSentry>[0];
type BeforeSendSpan = NonNullable<SentryOptions["beforeSendSpan"]>;
type BeforeSendTransaction = NonNullable<SentryOptions["beforeSendTransaction"]>;
type SpanJSON = Parameters<BeforeSendSpan>[0];
type TransactionEvent = Parameters<BeforeSendTransaction>[0];

export const REDACTED = "[Filtered]";

const SENSITIVE_KEYS = new Set([
  "_csrf",
  "_csrf_token",
  "_session",
  "_xsrf",
  "access_token",
  "aiohttp_session",
  "ai_service_api_key",
  "apikey",
  "api_key",
  "apipay_api_key",
  "apipay_webhook_secret",
  "authorization",
  "auth",
  "camera_pass",
  "connect.sid",
  "cookie",
  "credentials",
  "csrf",
  "csrftoken",
  "csrf_token",
  "csrfmiddlewaretoken",
  "ip_address",
  "mysql_pwd",
  "passwd",
  "password",
  "privatekey",
  "private_key",
  "proxy_authorization",
  "phpsessid",
  "remote_addr",
  "refresh",
  "refresh_token",
  "secret",
  "secret_key",
  "session",
  "sessionid",
  "session_id",
  "sentry_auth_token",
  "set_cookie",
  "symfony",
  "token",
  "user_session",
  "webhook_signature",
  "http_x_api_key",
  "http_x_webhook_signature",
  "x_api_key",
  "x_csrftoken",
  "x_csrf_token",
  "x_forwarded_for",
  "x_real_ip",
  "x_webhook_signature",
  "xsrf_token",
]);

const SENSITIVE_KEY_PREFIXES = ["password_", "passwd_", "secret_", "credential_", "credentials_"];
const SENSITIVE_KEY_SUFFIXES = [
  "_password",
  "_passwd",
  "_secret",
  "_credential",
  "_credentials",
  "_token",
  "_api_key",
  "_private_key",
  "_authorization",
  "_webhook_signature",
];

function normalizedKey(value: string): string {
  return value
    .trim()
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .toLowerCase()
    .replaceAll("-", "_");
}

function isSensitiveKey(value: string): boolean {
  const normalized = normalizedKey(value);
  return (
    SENSITIVE_KEYS.has(normalized) ||
    SENSITIVE_KEY_PREFIXES.some((prefix) => normalized.startsWith(prefix)) ||
    SENSITIVE_KEY_SUFFIXES.some((suffix) => normalized.endsWith(suffix))
  );
}

function withoutUrlPrivateParts(value: string): string {
  if (!value.startsWith("http://") && !value.startsWith("https://") && !value.startsWith("/")) {
    return value;
  }

  const queryIndex = value.indexOf("?");
  const fragmentIndex = value.indexOf("#");
  const privatePartIndexes = [queryIndex, fragmentIndex].filter((index) => index >= 0);
  return privatePartIndexes.length > 0 ? value.slice(0, Math.min(...privatePartIndexes)) : value;
}

export function scrubSensitiveData(value: unknown): unknown {
  if (Array.isArray(value)) {
    if (value.length === 2 && typeof value[0] === "string" && isSensitiveKey(value[0])) {
      return [value[0], REDACTED];
    }
    return value.map(scrubSensitiveData);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, isSensitiveKey(key) ? REDACTED : scrubSensitiveData(item)]),
    );
  }
  if (typeof value === "string") {
    return withoutUrlPrivateParts(value);
  }
  return value;
}

const PRIVATE_REQUEST_KEYS = new Set(["headers", "cookies", "query_string", "data", "body", "form_data", "files"]);
const PRIVATE_RESPONSE_KEYS = new Set(["headers", "cookies", "data", "body"]);
const PRIVATE_TRACE_ATTRIBUTE_KEYS = new Set([
  "http_fragment",
  "http_query",
  "http_query_params",
  "http_query_string",
  "url_fragment",
  "url_query",
  "url_query_params",
  "url_query_string",
]);
const PRIVATE_TRACE_REQUEST_FIELDS = [
  "header",
  "headers",
  "cookie",
  "cookies",
  "query",
  "fragment",
  "data",
  "body",
  "form_data",
  "file",
  "files",
  "payload",
];
const PRIVATE_TRACE_RESPONSE_FIELDS = [
  "header",
  "headers",
  "cookie",
  "cookies",
  "data",
  "body",
  "form_data",
  "file",
  "files",
  "payload",
];

function stripHttpMetadata(value: unknown, insideRequest = false, insideResponse = false): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => stripHttpMetadata(item, insideRequest, insideResponse));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).flatMap(([key, item]) => {
        const normalized = normalizedKey(key);
        if (insideRequest && PRIVATE_REQUEST_KEYS.has(normalized)) {
          return [];
        }
        if (insideResponse && PRIVATE_RESPONSE_KEYS.has(normalized)) {
          return [];
        }
        return [
          [
            key,
            stripHttpMetadata(
              item,
              insideRequest || normalized === "request",
              insideResponse || normalized === "response",
            ),
          ],
        ];
      }),
    );
  }
  return value;
}

function normalizedTraceAttributeKey(value: string): string {
  return normalizedKey(value)
    .replace(/[./:\s]+/g, "_")
    .replace(/_+/g, "_");
}

function hasPrivateTraceField(normalized: string, scope: "request" | "response", fields: string[]): boolean {
  return [`${scope}_`, `http_${scope}_`].some((prefix) => {
    if (!normalized.startsWith(prefix)) {
      return false;
    }
    const field = normalized.slice(prefix.length);
    return fields.some((privateField) => field === privateField || field.startsWith(`${privateField}_`));
  });
}

function isPrivateTraceAttributeKey(value: string): boolean {
  const normalized = normalizedTraceAttributeKey(value);
  return (
    PRIVATE_TRACE_ATTRIBUTE_KEYS.has(normalized) ||
    hasPrivateTraceField(normalized, "request", PRIVATE_TRACE_REQUEST_FIELDS) ||
    hasPrivateTraceField(normalized, "response", PRIVATE_TRACE_RESPONSE_FIELDS)
  );
}

function stripPrivateTraceAttributes(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(stripPrivateTraceAttributes);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).flatMap(([key, item]) =>
        isPrivateTraceAttributeKey(key) ? [] : [[key, stripPrivateTraceAttributes(item)]],
      ),
    );
  }
  return value;
}

function scrubEventLike(value: unknown): unknown {
  return stripHttpMetadata(scrubSensitiveData(value));
}

function scrubTracePayload(value: unknown): unknown {
  return stripPrivateTraceAttributes(scrubEventLike(value));
}

export function beforeSend(event: ErrorEvent): ErrorEvent {
  return scrubTracePayload(event) as ErrorEvent;
}

export function beforeSendLog(log: Log): Log {
  return scrubTracePayload(log) as Log;
}

export function beforeSendTransaction(event: TransactionEvent): TransactionEvent {
  return scrubTracePayload(event) as TransactionEvent;
}

export function beforeSendSpan(span: SpanJSON): SpanJSON {
  return scrubTracePayload(span) as SpanJSON;
}

export function envFlag(value: string | undefined): boolean {
  return ["1", "true", "yes", "on"].includes(value?.trim().toLowerCase() ?? "");
}

export function sampleRate(value: string | undefined): number {
  const parsed = Number(value ?? "0");
  return Number.isFinite(parsed) && parsed >= 0 && parsed <= 1 ? parsed : 0;
}

// Explicit collection policy for both browser and optional server clients.
// Request/response bodies, cookies, query strings, local variables and database
// values remain out of telemetry even if an SDK default changes later.
export const privateDataCollection = {
  userInfo: false,
  cookies: false,
  httpHeaders: { request: false, response: false },
  httpBodies: [],
  urlQueryParams: false,
  graphQL: { document: false, variables: false },
  genAI: { inputs: false, outputs: false },
  databaseQueryData: false,
  stackFrameVariables: false,
  frameContextLines: 0,
};
