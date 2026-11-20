import { X } from 'lucide-react';
import type { SyncStatus, Toast } from '../types/scheduling';

const LABELS: Record<SyncStatus, string> = {
  live: 'Live Sync Active',
  connecting: 'Connecting...',
  reconnecting: 'Reconnecting...',
  offline: 'Offline',
};

const DOT: Record<SyncStatus, string> = {
  live: 'bg-emerald-500',
  connecting: 'bg-amber-400',
  reconnecting: 'bg-amber-400',
  offline: 'bg-red-500',
};

export function LiveStatusBadge({ status }: { status: SyncStatus }) {
  return (
    <span role="status" aria-live="polite" className="inline-flex items-center gap-1.5 text-xs text-slate-600">
      <span className={`h-2 w-2 rounded-full ${DOT[status]}`} />
      {LABELS[status]}
    </span>
  );
}

export function ToastStack({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  return (
    <div className="fixed bottom-4 right-4 z-50 flex w-72 flex-col gap-2" aria-live="polite" aria-label="Live updates">
      {toasts.map((t) => (
        <div key={t.id} className="flex items-start gap-2 rounded bg-slate-800 px-3 py-2 text-sm text-white shadow">
          <span className="flex-1">{t.message}</span>
          <button type="button" onClick={() => onDismiss(t.id)} className="text-slate-400 hover:text-white" aria-label="Dismiss">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
