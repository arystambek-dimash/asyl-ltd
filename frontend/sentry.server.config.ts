import * as Sentry from "@sentry/nextjs";
import {
  beforeSend,
  beforeSendLog,
  beforeSendSpan,
  beforeSendTransaction,
  envFlag,
  privateDataCollection,
  sampleRate,
} from "./src/lib/sentry-config";

const dsn = process.env.SENTRY_FRONTEND_SERVER_DSN?.trim();

// Production's frontend container has no external route. Supplying a DSN is
// therefore necessary but not sufficient: deployment must also provide an
// explicitly reviewed relay or controlled egress path.
if (dsn) {
  Sentry.init({
    dsn,
    release: process.env.APP_RELEASE || "development",
    environment: process.env.APP_ENVIRONMENT || "development",
    initialScope: { tags: { service: process.env.APP_SERVICE || "frontend-server" } },
    sendDefaultPii: false,
    dataCollection: privateDataCollection,
    beforeSend,
    beforeSendLog,
    beforeSendSpan,
    beforeSendTransaction,
    enableLogs: envFlag(process.env.SENTRY_ENABLE_LOGS),
    tracesSampleRate: sampleRate(process.env.SENTRY_TRACES_SAMPLE_RATE),
    profilesSampleRate: sampleRate(process.env.SENTRY_PROFILES_SAMPLE_RATE),
    profileSessionSampleRate: 0,
  });
}
