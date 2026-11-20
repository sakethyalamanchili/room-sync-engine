import AvailabilityGrid from './components/AvailabilityGrid';
import { ToastStack } from './components/LiveStatusBanner';
import { useRoomSync } from './hooks/useRoomSync';

export default function App() {
  const { status, toasts, dismissToast } = useRoomSync();

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b-4 border-fau-red bg-fau-blue text-white">
        <div className="mx-auto flex max-w-screen-2xl items-center gap-3 px-4 py-3">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold leading-tight">FAU EECS RoomSync</h1>
            <p className="truncate text-xs text-blue-100">
              Dept. of Electrical Engineering &amp; Computer Science, Florida Atlantic University
            </p>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-screen-2xl px-4 py-6">
        <AvailabilityGrid syncStatus={status} />
        <p className="mt-4 text-center text-xs text-slate-400">
          Student project, not affiliated with or endorsed by Florida Atlantic University.
        </p>
      </main>
      <ToastStack toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
