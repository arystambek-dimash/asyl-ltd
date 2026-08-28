"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { AxiosError } from "axios";

import { apiError, isCanceledRequest } from "@/lib/api";
import {
  getModelTestInfo,
  getModelTestJob,
  startModelTest,
  type ModelTestAccepted,
  type ModelTestInfo,
  type ModelTestJob,
  type ModelTestStartOptions,
} from "@/lib/model-tests";

const POLL_INTERVAL_MS = 750;
const ACTIVE_JOB_STORAGE_KEY = "asyl:model-test-active:v1";
const JOB_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function saveActiveJob(jobId: string | null) {
  try {
    if (jobId) sessionStorage.setItem(ACTIVE_JOB_STORAGE_KEY, jobId);
    else sessionStorage.removeItem(ACTIVE_JOB_STORAGE_KEY);
  } catch {
    // Storage may be disabled. The active in-memory run still works.
  }
}

function restoredActiveJob(): string | null {
  try {
    const jobId = sessionStorage.getItem(ACTIVE_JOB_STORAGE_KEY) ?? "";
    return JOB_ID_RE.test(jobId) ? jobId : null;
  } catch {
    return null;
  }
}

export function useModelTestRunner() {
  const [info, setInfo] = useState<ModelTestInfo | null>(null);
  const [infoLoading, setInfoLoading] = useState(true);
  const [infoError, setInfoError] = useState("");
  const [accepted, setAccepted] = useState<ModelTestAccepted | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<ModelTestJob | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [error, setError] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const infoController = useRef<AbortController | null>(null);
  const uploadController = useRef<AbortController | null>(null);
  const runGeneration = useRef(0);
  const activeRef = useRef(false);

  const loadInfo = useCallback(async () => {
    infoController.current?.abort();
    const controller = new AbortController();
    infoController.current = controller;
    setInfoLoading(true);
    setInfoError("");
    try {
      setInfo(await getModelTestInfo(controller.signal));
    } catch (cause) {
      if (!isCanceledRequest(cause)) setInfoError(apiError(cause));
    } finally {
      if (infoController.current === controller) {
        infoController.current = null;
        setInfoLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void loadInfo();
    const restored = restoredActiveJob();
    if (restored) setJobId(restored);
    return () => {
      runGeneration.current += 1;
      infoController.current?.abort();
      infoController.current = null;
      uploadController.current?.abort();
      uploadController.current = null;
    };
  }, [loadInfo]);

  useEffect(() => {
    if (!jobId) return;
    const generation = runGeneration.current;
    let disposed = false;
    let terminal = false;
    let running = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;

    const schedule = () => {
      if (disposed || terminal) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        void poll();
      }, POLL_INTERVAL_MS);
    };

    const poll = async () => {
      if (disposed || terminal || running) return;
      if (document.hidden) {
        schedule();
        return;
      }
      running = true;
      controller = new AbortController();
      try {
        const next = await getModelTestJob(jobId, 0, 100, controller.signal);
        if (disposed || generation !== runGeneration.current) return;
        setJob(next);
        setError("");
        terminal = next.status === "completed" || next.status === "failed";
        if (terminal) saveActiveJob(null);
      } catch (cause) {
        if (disposed || isCanceledRequest(cause) || generation !== runGeneration.current) return;
        if ((cause as AxiosError).response?.status === 404) {
          terminal = true;
          saveActiveJob(null);
          setAccepted(null);
          setJobId(null);
        }
        setError(apiError(cause));
      } finally {
        running = false;
        controller = null;
        schedule();
      }
    };

    const pollNow = () => {
      if (disposed || terminal || document.hidden) return;
      if (timer) clearTimeout(timer);
      timer = null;
      void poll();
    };

    document.addEventListener("visibilitychange", pollNow);
    window.addEventListener("online", pollNow);
    void poll();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", pollNow);
      window.removeEventListener("online", pollNow);
    };
  }, [jobId]);

  const start = useCallback(async (file: File, options: ModelTestStartOptions) => {
    if (activeRef.current) return null;
    const generation = ++runGeneration.current;
    const controller = new AbortController();
    uploadController.current = controller;
    activeRef.current = true;
    setSubmitting(true);
    setUploadProgress(0);
    setAccepted(null);
    setJob(null);
    setJobId(null);
    setError("");
    try {
      const result = await startModelTest(
        file,
        options,
        (loaded, total) => {
          if (generation !== runGeneration.current) return;
          const denominator = total > 0 ? total : file.size;
          setUploadProgress(Math.min(100, (loaded / denominator) * 100));
        },
        controller.signal,
      );
      if (generation !== runGeneration.current) return null;
      setUploadProgress(100);
      setAccepted(result);
      setJobId(result.job_id);
      saveActiveJob(result.job_id);
      return result;
    } catch (cause) {
      if (generation === runGeneration.current && !isCanceledRequest(cause)) {
        setError(apiError(cause));
      }
      return null;
    } finally {
      if (generation === runGeneration.current) {
        uploadController.current = null;
        activeRef.current = false;
        setSubmitting(false);
      }
    }
  }, []);

  const cancelUpload = useCallback(() => {
    if (!uploadController.current) return;
    runGeneration.current += 1;
    uploadController.current.abort();
    uploadController.current = null;
    activeRef.current = false;
    setSubmitting(false);
    setUploadProgress(0);
    setError("Загрузка отменена");
  }, []);

  const loadMoreEvents = useCallback(async () => {
    if (!job?.page.has_more || loadingMore) return;
    const generation = runGeneration.current;
    setLoadingMore(true);
    try {
      const next = await getModelTestJob(job.job_id, job.page.next_after_event, 500);
      if (generation !== runGeneration.current) return;
      setJob((current) =>
        current?.job_id === next.job_id ? { ...next, events: [...current.events, ...next.events] } : current,
      );
    } catch (cause) {
      if (!isCanceledRequest(cause)) setError(apiError(cause));
    } finally {
      if (generation === runGeneration.current) setLoadingMore(false);
    }
  }, [job, loadingMore]);

  const status = job?.status ?? accepted?.status ?? (jobId ? "queued" : null);
  const active = submitting || status === "queued" || status === "running";
  activeRef.current = active;

  return {
    info,
    infoLoading,
    infoError,
    reloadInfo: loadInfo,
    accepted,
    job,
    status,
    active,
    submitting,
    uploadProgress,
    error,
    start,
    cancelUpload,
    loadingMore,
    loadMoreEvents,
  };
}
