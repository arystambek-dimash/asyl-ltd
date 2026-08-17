import { describe, expect, it } from "vitest";
import {
  REDACTED,
  beforeSend,
  beforeSendLog,
  beforeSendSpan,
  beforeSendTransaction,
  envFlag,
  privateDataCollection,
  sampleRate,
  scrubSensitiveData,
} from "./sentry-config";

describe("Sentry privacy configuration", () => {
  it("recursively scrubs normalized sensitive keys without mutating the source", () => {
    const source = {
      request: {
        headers: {
          Authorization: "Bearer secret",
          "X-Request-ID": "safe",
          "X-Api-Key": "service-secret",
          "X-Webhook-Signature": "sha256=secret",
        },
        data: [
          { password: "hidden", label: "safe" },
          { "api-key": "hidden" },
          ["HTTP_X_WEBHOOK_SIGNATURE", "sha256=secret"],
          {
            password_confirmation: "hidden",
            client_secret: "hidden",
            privateKey: "hidden",
            apiToken: "hidden",
            auth_credentials: "hidden",
            mysql_pwd: "hidden",
            sessionId: "hidden",
            xForwardedFor: "203.0.113.10",
            xCsrfToken: "hidden",
            "XSRF-TOKEN": "hidden",
            _csrf: "hidden",
            _xsrf: "hidden",
            PHPSESSID: "hidden",
            "connect.sid": "hidden",
            aiohttp_session: "hidden",
            token_count: 12,
            public_key: "safe-public-key",
          },
        ],
      },
    };

    expect(scrubSensitiveData(source)).toEqual({
      request: {
        headers: {
          Authorization: REDACTED,
          "X-Request-ID": "safe",
          "X-Api-Key": REDACTED,
          "X-Webhook-Signature": REDACTED,
        },
        data: [
          { password: REDACTED, label: "safe" },
          { "api-key": REDACTED },
          ["HTTP_X_WEBHOOK_SIGNATURE", REDACTED],
          {
            password_confirmation: REDACTED,
            client_secret: REDACTED,
            privateKey: REDACTED,
            apiToken: REDACTED,
            auth_credentials: REDACTED,
            mysql_pwd: REDACTED,
            sessionId: REDACTED,
            xForwardedFor: REDACTED,
            xCsrfToken: REDACTED,
            "XSRF-TOKEN": REDACTED,
            _csrf: REDACTED,
            _xsrf: REDACTED,
            PHPSESSID: REDACTED,
            "connect.sid": REDACTED,
            aiohttp_session: REDACTED,
            token_count: 12,
            public_key: "safe-public-key",
          },
        ],
      },
    });
    expect(source.request.headers.Authorization).toBe("Bearer secret");
  });

  it("keeps logs and sample rates opt-in", () => {
    expect(envFlag(undefined)).toBe(false);
    expect(envFlag("0")).toBe(false);
    expect(envFlag("true")).toBe(true);
    expect(sampleRate(undefined)).toBe(0);
    expect(sampleRate("0.25")).toBe(0.25);
    expect(sampleRate("invalid")).toBe(0);
    expect(sampleRate("2")).toBe(0);
  });

  it("removes request metadata and URL query/fragment data from events", () => {
    const scrubbed = beforeSend({
      type: undefined,
      request: {
        url: "https://example.test/api/orders/?search=customer-phone#access_token",
        query_string: "search=customer-phone",
        cookies: { session: "secret" },
        headers: {
          Referer: "https://example.test/orders?search=customer-phone#access_token",
          "X-Request-ID": "safe-id",
        },
        data: { customer_phone: "+77001234567" },
        body: "customer_phone=+77001234567",
        form_data: { customer_phone: "+77001234567" },
        files: [{ name: "private.pdf" }],
      },
      breadcrumbs: [{ data: { url: "/orders?search=customer-phone#access_token" } }],
      extra: {
        response: {
          status_code: 422,
          headers: { "Set-Cookie": "session=secret" },
          cookies: { session: "secret" },
          data: { customer_phone: "+77001234567" },
          body: "customer_phone=+77001234567",
        },
      },
    } as unknown as Parameters<typeof beforeSend>[0]);

    expect(scrubbed.request).toEqual({ url: "https://example.test/api/orders/" });
    expect(scrubbed.breadcrumbs?.[0]?.data?.url).toBe("/orders");
    expect(scrubbed.extra).toEqual({ response: { status_code: 422 } });
    expect(JSON.stringify(scrubbed)).not.toContain("customer-phone");
    expect(JSON.stringify(scrubbed)).not.toContain("access_token");
    expect(JSON.stringify(scrubbed)).not.toContain("Referer");
    expect(JSON.stringify(scrubbed)).not.toContain("+77001234567");
  });

  it("applies the same fail-closed request policy to structured logs", () => {
    const scrubbed = beforeSendLog({
      level: "info",
      message: "safe",
      attributes: {
        request: {
          url: "/api/orders?search=customer-phone#access_token",
          query_string: "search=customer-phone",
          cookies: "session=secret",
          data: { customer_phone: "+77001234567" },
          body: "customer_phone=+77001234567",
          form_data: { customer_phone: "+77001234567" },
          files: [{ name: "private.pdf" }],
          headers: {
            Referer: "https://example.test/orders?search=customer-phone#access_token",
          },
        },
        response: {
          status_code: 422,
          headers: { "Set-Cookie": "session=secret" },
          cookies: { session: "secret" },
          data: { customer_phone: "+77001234567" },
          body: "customer_phone=+77001234567",
        },
        callbackUrl: "https://example.test/done?search=customer-phone#access_token",
        "http.query": "search=customer-phone",
        "http.fragment": "access_token",
        "http.request.body": "customer_phone=+77001234567",
        "http.response.body": "customer_phone=+77001234567",
        "http.request.method": "GET",
      },
    });

    expect(scrubbed.attributes).toEqual({
      request: { url: "/api/orders" },
      response: { status_code: 422 },
      callbackUrl: "https://example.test/done",
      "http.request.method": "GET",
    });
    expect(scrubbed.attributes).not.toHaveProperty("http.query");
    expect(scrubbed.attributes).not.toHaveProperty("http.fragment");
    expect(scrubbed.attributes).not.toHaveProperty("http.request.body");
    expect(scrubbed.attributes).not.toHaveProperty("http.response.body");
    expect(JSON.stringify(scrubbed)).not.toContain("+77001234567");
    expect(privateDataCollection.frameContextLines).toBe(0);
  });

  it("scrubs transaction events and their nested span attributes", () => {
    const scrubbed = beforeSendTransaction({
      type: "transaction",
      transaction: "GET /api/orders",
      request: {
        url: "https://example.test/api/orders?search=customer-phone#access_token",
        headers: { Referer: "https://example.test/private?customer-phone" },
        data: { customer_phone: "+77001234567" },
      },
      spans: [
        {
          data: {
            "http.url": "https://example.test/api/orders?search=customer-phone#access_token",
            httpQuery: "search=customer-phone",
            "http.fragment": "access_token",
            url_query: "search=customer-phone",
            urlFragment: "access_token",
            "http.request.body": "customer_phone=+77001234567",
            "http.request.body.size": 32,
            httpRequestHeaders: "Referer: https://example.test/private",
            "http.response.data": "customer_phone=+77001234567",
            "http.response.body.size": 64,
            httpResponseCookies: "session=secret",
            "http.request.method": "GET",
            "http.response.status_code": 200,
            token_count: 4,
            public_key: "safe-public-key",
          },
          description: "/api/orders?search=customer-phone#access_token",
          span_id: "0123456789abcdef",
          start_timestamp: 1,
          trace_id: "0123456789abcdef0123456789abcdef",
        },
      ],
    } as unknown as Parameters<typeof beforeSendTransaction>[0]);

    expect(scrubbed.request).toEqual({ url: "https://example.test/api/orders" });
    expect(scrubbed.spans?.[0]?.description).toBe("/api/orders");
    expect(scrubbed.spans?.[0]?.data).toEqual({
      "http.url": "https://example.test/api/orders",
      "http.request.method": "GET",
      "http.response.status_code": 200,
      token_count: 4,
      public_key: "safe-public-key",
    });
    expect(JSON.stringify(scrubbed)).not.toContain("customer-phone");
    expect(JSON.stringify(scrubbed)).not.toContain("access_token");
    expect(JSON.stringify(scrubbed)).not.toContain("+77001234567");
    expect(JSON.stringify(scrubbed)).not.toContain("Referer");
  });

  it("scrubs standalone spans without requiring streamed trace lifecycle", () => {
    const scrubbed = beforeSendSpan({
      data: {
        "url.full": "https://example.test/done?search=customer-phone#access_token",
        "url.query": "search=customer-phone",
        urlFragment: "access_token",
        "http.query_string": "search=customer-phone",
        "request.formData": "customer_phone=+77001234567",
        "request.files": "private.pdf",
        "response.headers": "Set-Cookie: session=secret",
        "response.body": "customer_phone=+77001234567",
        "http.request.method": "POST",
        "http.response.status_code": 202,
      },
      description: "https://example.test/done?search=customer-phone#access_token",
      span_id: "fedcba9876543210",
      start_timestamp: 1,
      trace_id: "fedcba9876543210fedcba9876543210",
    });

    expect(scrubbed.description).toBe("https://example.test/done");
    expect(scrubbed.data).toEqual({
      "url.full": "https://example.test/done",
      "http.request.method": "POST",
      "http.response.status_code": 202,
    });
    expect(JSON.stringify(scrubbed)).not.toContain("customer-phone");
    expect(JSON.stringify(scrubbed)).not.toContain("access_token");
    expect(JSON.stringify(scrubbed)).not.toContain("+77001234567");
    expect(JSON.stringify(scrubbed)).not.toContain("Set-Cookie");
  });
});
