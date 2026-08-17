import * as Sentry from "@sentry/nextjs";
import {
  beforeSend,
  beforeSendLog,
  beforeSendSpan,
  beforeSendTransaction,
  envFlag,
  privateDataCollection,
  sampleRate,
} from "@/lib/sentry-config";

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN?.trim();

if (dsn) {
  Sentry.init({
    dsn,
    release: process.env.NEXT_PUBLIC_APP_RELEASE || "development",
    environment: process.env.NEXT_PUBLIC_APP_ENVIRONMENT || "development",
    initialScope: { tags: { service: "frontend-browser" } },
    sendDefaultPii: false,
    dataCollection: privateDataCollection,
    beforeSend,
    beforeSendLog,
    beforeSendSpan,
    beforeSendTransaction,
    enableLogs: envFlag(process.env.NEXT_PUBLIC_SENTRY_ENABLE_LOGS),
    tracesSampleRate: sampleRate(process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE),
    profilesSampleRate: 0,
    replaysSessionSampleRate: 0,
    replaysOnErrorSampleRate: 0,
  });
}

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
