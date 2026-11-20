import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { WS_URL } from '../api/client';
import type { SlotAction, SlotUpdatedEvent, SyncStatus, Toast } from '../types/scheduling';

const PING_INTERVAL_MS = 25_000;
const MAX_BACKOFF_MS = 30_000;
const OFFLINE_AFTER_ATTEMPTS = 4;
const MAX_TOASTS = 3;
const TOAST_TTL_MS = 4_000;

const ACTION_LABEL: Record<SlotAction, string> = {
  created: 'booking created',
  updated: 'booking updated',
  cancelled: 'booking cancelled',
  checked_in: 'checked in',
  auto_released: 'auto-released (no check-in)',
};

function isSlotUpdated(value: unknown): value is SlotUpdatedEvent {
  if (typeof value !== 'object' || value === null) return false;
  const v = value as Record<string, unknown>;
  return v.event === 'SLOT_UPDATED' && typeof v.action === 'string' && typeof v.room_name === 'string';
}

function backoffDelay(attempt: number): number {
  const base = Math.min(MAX_BACKOFF_MS, 1000 * 2 ** Math.max(0, attempt - 1));
  const jitter = Math.random() * 0.3 * base;
  return Math.min(MAX_BACKOFF_MS, base + jitter);
}

export interface RoomSync {
  status: SyncStatus;
  toasts: Toast[];
  dismissToast: (id: number) => void;
}

export function useRoomSync(): RoomSync {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<SyncStatus>('connecting');
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastId = useRef(0);
  const toastTimers = useRef(new Map<number, number>());

  const dismissToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = toastTimers.current.get(id);
    if (timer !== undefined) {
      window.clearTimeout(timer);
      toastTimers.current.delete(id);
    }
  }, []);

  const pushToast = useCallback(
    (message: string) => {
      toastId.current += 1;
      const id = toastId.current;
      setToasts((prev) => [...prev, { id, message }].slice(-MAX_TOASTS));
      toastTimers.current.set(id, window.setTimeout(() => dismissToast(id), TOAST_TTL_MS));
    },
    [dismissToast],
  );

  useEffect(() => {
    let socket: WebSocket | null = null;
    let attempt = 0;
    let disposed = false;
    let pingTimer: number | undefined;
    let retryTimer: number | undefined;

    const scheduleReconnect = () => {
      if (disposed) return;
      attempt += 1;
      setStatus(attempt >= OFFLINE_AFTER_ATTEMPTS || !navigator.onLine ? 'offline' : 'reconnecting');
      retryTimer = window.setTimeout(connect, backoffDelay(attempt));
    };

    function connect() {
      if (disposed) return;
      try {
        socket = new WebSocket(WS_URL);
      } catch {
        scheduleReconnect();
        return;
      }

      socket.onopen = () => {
        const wasReconnect = attempt > 0;
        attempt = 0;
        setStatus('live');
        pingTimer = window.setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) socket.send('ping');
        }, PING_INTERVAL_MS);
        // might have missed events while we were disconnected
        if (wasReconnect) void queryClient.invalidateQueries({ queryKey: ['availability'] });
      };

      socket.onmessage = (ev: MessageEvent<string>) => {
        if (ev.data === 'pong') return;
        let parsed: unknown;
        try {
          parsed = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (!isSlotUpdated(parsed)) return;
        void queryClient.invalidateQueries({ queryKey: ['availability'] });
        pushToast(`${parsed.room_name}: ${ACTION_LABEL[parsed.action] ?? parsed.action}`);
      };

      socket.onclose = () => {
        window.clearInterval(pingTimer);
        socket = null;
        scheduleReconnect();
      };

      socket.onerror = () => {
        socket?.close();
      };
    }

    connect();
    const timers = toastTimers.current;

    return () => {
      disposed = true;
      window.clearInterval(pingTimer);
      window.clearTimeout(retryTimer);
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
      timers.forEach((t) => window.clearTimeout(t));
      timers.clear();
    };
  }, [queryClient, pushToast]);

  return { status, toasts, dismissToast };
}
