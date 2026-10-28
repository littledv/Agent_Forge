"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, getRunDetail, listRuns } from "./api";
import type { RunDetailSnapshot, RunListItem, RunState } from "./contracts";

const DETAIL_INTERVAL_MS = 1_000;
const LIST_INTERVAL_MS = 2_000;

const TERMINAL_STATES = new Set<RunState>([
  "SUCCEEDED",
  "FAILED",
  "DENIED",
  "CANCELLED",
  "TIMED_OUT",
  "BUDGET_EXHAUSTED",
  "NEEDS_ATTENTION",
]);

type RunPollingOptions = {
  onSelectedRunMissing: () => void;
};

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function serviceMessage(error: unknown): string {
  if (error instanceof ApiError && error.status !== 503) {
    return error.detail;
  }
  return "AgentForge is temporarily unreachable. Showing the last application projection.";
}

export function useRunPolling(
  selectedRunId: string | null,
  { onSelectedRunMissing }: RunPollingOptions,
) {
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [detail, setDetail] = useState<RunDetailSnapshot | null>(null);
  const [detailObservedAt, setDetailObservedAt] = useState(0);
  const [listLoading, setListLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [listRevision, setListRevision] = useState(0);
  const [detailRevision, setDetailRevision] = useState(0);
  const listGeneration = useRef(0);
  const detailGeneration = useRef(0);
  const previousSelection = useRef<string | null>(null);
  const detailRefreshWaiters = useRef<
    Array<{
      resolve: (detail: RunDetailSnapshot) => void;
      reject: (error: unknown) => void;
    }>
  >([]);

  const refreshRuns = useCallback(() => {
    setListRevision((revision) => revision + 1);
  }, []);

  const refreshDetail = useCallback(() => {
    return new Promise<RunDetailSnapshot>((resolve, reject) => {
      detailRefreshWaiters.current.push({ resolve, reject });
      setDetailRevision((revision) => revision + 1);
    });
  }, []);

  const resolveDetailRefresh = useCallback((value: RunDetailSnapshot) => {
    const waiters = detailRefreshWaiters.current.splice(0);
    for (const waiter of waiters) {
      waiter.resolve(value);
    }
  }, []);

  const rejectDetailRefresh = useCallback((error: unknown) => {
    const waiters = detailRefreshWaiters.current.splice(0);
    for (const waiter of waiters) {
      waiter.reject(error);
    }
  }, []);

  useEffect(() => {
    const generation = ++listGeneration.current;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    async function pollList() {
      controller = new AbortController();
      try {
        const nextRuns = await listRuns(controller.signal);
        if (!disposed && generation === listGeneration.current) {
          setRuns(nextRuns);
          setListError(null);
        }
      } catch (caught) {
        if (!disposed && generation === listGeneration.current && !isAbort(caught)) {
          setListError(serviceMessage(caught));
        }
      } finally {
        if (!disposed && generation === listGeneration.current) {
          setListLoading(false);
          timer = setTimeout(() => void pollList(), LIST_INTERVAL_MS);
        }
      }
    }

    timer = setTimeout(() => void pollList(), 0);
    return () => {
      disposed = true;
      if (timer !== null) {
        clearTimeout(timer);
      }
      controller?.abort();
    };
  }, [listRevision]);

  useEffect(() => {
    const selectionChanged = previousSelection.current !== selectedRunId;
    previousSelection.current = selectedRunId;
    const generation = ++detailGeneration.current;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;

    if (selectedRunId === null) {
      timer = setTimeout(() => {
        if (!disposed && generation === detailGeneration.current) {
          setDetail(null);
          setDetailObservedAt(0);
          setDetailError(null);
          setDetailLoading(false);
          rejectDetailRefresh(new ApiError(404, "No run is selected"));
        }
      }, 0);
      return () => {
        disposed = true;
        if (timer !== null) {
          clearTimeout(timer);
        }
      };
    }
    const runId = selectedRunId;

    async function pollDetail() {
      controller = new AbortController();
      let continuePolling = true;
      try {
        const nextDetail = await getRunDetail(runId, controller.signal);
        if (!disposed && generation === detailGeneration.current) {
          setDetail(nextDetail);
          setDetailObservedAt(Date.now());
          setDetailError(null);
          resolveDetailRefresh(nextDetail);
          continuePolling = !TERMINAL_STATES.has(nextDetail.run.state);
        }
      } catch (caught) {
        if (disposed || generation !== detailGeneration.current || isAbort(caught)) {
          return;
        }
        if (caught instanceof ApiError && caught.status === 404) {
          continuePolling = false;
          setDetail(null);
          onSelectedRunMissing();
          refreshRuns();
          rejectDetailRefresh(caught);
        } else {
          setDetailError(serviceMessage(caught));
          rejectDetailRefresh(caught);
        }
      } finally {
        if (!disposed && generation === detailGeneration.current) {
          setDetailLoading(false);
          if (continuePolling) {
            timer = setTimeout(() => void pollDetail(), DETAIL_INTERVAL_MS);
          }
        }
      }
    }

    timer = setTimeout(() => {
      if (selectionChanged) {
        setDetail(null);
        setDetailObservedAt(0);
        setDetailError(null);
      }
      setDetailLoading(true);
      void pollDetail();
    }, 0);
    return () => {
      disposed = true;
      if (timer !== null) {
        clearTimeout(timer);
      }
      controller?.abort();
    };
  }, [
    detailRevision,
    onSelectedRunMissing,
    rejectDetailRefresh,
    refreshRuns,
    resolveDetailRefresh,
    selectedRunId,
  ]);

  return {
    runs,
    detail,
    detailObservedAt,
    listLoading,
    detailLoading,
    serviceNotice: detailError ?? listError,
    refreshRuns,
    refreshDetail,
  };
}
