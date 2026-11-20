import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { DateTime } from 'luxon';
import { X } from 'lucide-react';
import { ApiError, api, newIdempotencyKey } from '../api/client';
import type { AvailabilityBooking, BookingCreateRequest, Room } from '../types/scheduling';

export type ModalState =
  | { kind: 'create'; room: Room; slotStart: DateTime; dayEnd: DateTime }
  | { kind: 'manage'; room: Room; booking: AvailabilityBooking; zone: string };

const DURATIONS = [30, 60, 90, 120] as const;
const STORAGE_NAME = 'roomsync.organizer_name';
const STORAGE_EMAIL = 'roomsync.organizer_email';

function readStored(key: string): string {
  try {
    return window.localStorage.getItem(key) ?? '';
  } catch {
    return '';
  }
}

function writeStored(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // private mode etc, not a big deal
  }
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error) return err.message;
  return 'Something went wrong';
}

interface Props {
  state: ModalState;
  onClose: () => void;
}

export default function BookingModal({ state, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/40 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="booking-modal-title"
        className="w-full max-w-md rounded-lg bg-white shadow-lg"
      >
        <div className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h2 id="booking-modal-title" className="text-base font-semibold text-slate-900">
              {state.kind === 'create' ? `Book ${state.room.name}` : state.booking.title}
            </h2>
            <p className="mt-0.5 text-xs text-slate-500">
              Floor {state.room.floor}, seats {state.room.capacity}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        {state.kind === 'create' ? (
          <CreateForm state={state} onClose={onClose} />
        ) : (
          <ManagePanel state={state} onClose={onClose} />
        )}
      </div>
    </div>
  );
}

function CreateForm({ state, onClose }: { state: Extract<ModalState, { kind: 'create' }>; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [title, setTitle] = useState('');
  const [name, setName] = useState(() => readStored(STORAGE_NAME));
  const [email, setEmail] = useState(() => readStored(STORAGE_EMAIL));
  const durations = useMemo(
    () => DURATIONS.filter((d) => state.slotStart.plus({ minutes: d }) <= state.dayEnd),
    [state.slotStart, state.dayEnd],
  );
  const [duration, setDuration] = useState<number>(durations.includes(60) ? 60 : durations[0] ?? 30);
  const [slotTaken, setSlotTaken] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);

  // same key if they retry the same thing, new key if they changed the form
  // (otherwise the server rejects it as a different payload)
  const attempt = useRef<{ key: string; payload: string | null }>({ key: newIdempotencyKey(), payload: null });

  useEffect(() => {
    titleRef.current?.focus();
  }, []);

  const mutation = useMutation({
    mutationFn: ({ body, key }: { body: BookingCreateRequest; key: string }) => api.createBooking(body, key),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['availability'] });
      onClose();
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        setSlotTaken(true);
        void queryClient.invalidateQueries({ queryKey: ['availability'] });
      }
    },
  });

  const start = state.slotStart;
  const end = start.plus({ minutes: duration });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setSlotTaken(false);
    const body: BookingCreateRequest = {
      room_id: state.room.id,
      organizer_name: name.trim(),
      organizer_email: email.trim(),
      title: title.trim(),
      start_time: start.toUTC().toISO({ suppressMilliseconds: true }) ?? '',
      end_time: end.toUTC().toISO({ suppressMilliseconds: true }) ?? '',
    };
    const serialized = JSON.stringify(body);
    if (attempt.current.payload !== null && attempt.current.payload !== serialized) {
      attempt.current = { key: newIdempotencyKey(), payload: null };
    }
    attempt.current.payload = serialized;
    writeStored(STORAGE_NAME, body.organizer_name);
    writeStored(STORAGE_EMAIL, body.organizer_email);
    mutation.mutate({ body, key: attempt.current.key });
  };

  const genericError = mutation.isError && !slotTaken ? errorMessage(mutation.error) : null;

  return (
    <form onSubmit={submit} className="space-y-4 px-5 py-4">
      <p className="rounded bg-slate-50 px-3 py-2 text-sm text-slate-700">
        {start.toFormat('ccc, LLL d')}, {start.toFormat('HH:mm')}-{end.toFormat('HH:mm')}{' '}
        <span className="text-xs text-slate-400">({start.offsetNameShort})</span>
      </p>

      <label className="block text-sm">
        <span className="font-medium text-slate-700">Title</span>
        <input
          ref={titleRef}
          required
          maxLength={255}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          placeholder="Sprint planning"
        />
      </label>
      <div className="grid grid-cols-2 gap-3">
        <label className="block text-sm">
          <span className="font-medium text-slate-700">Your name</span>
          <input
            required
            maxLength={120}
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </label>
        <label className="block text-sm">
          <span className="font-medium text-slate-700">Email</span>
          <input
            required
            type="email"
            maxLength={180}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </label>
      </div>
      <label className="block text-sm">
        <span className="font-medium text-slate-700">Duration</span>
        <select
          value={duration}
          onChange={(e) => setDuration(Number(e.target.value))}
          className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          {durations.map((d) => (
            <option key={d} value={d}>
              {d} minutes
            </option>
          ))}
        </select>
      </label>

      {slotTaken && (
        <p role="alert" className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
          That slot was just taken, pick another time.
        </p>
      )}
      {genericError && (
        <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {genericError}
        </p>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onClose}
          className="rounded-md px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100"
        >
          Close
        </button>
        <button
          type="submit"
          disabled={mutation.isPending}
          className="rounded-md bg-fau-blue px-4 py-2 text-sm font-medium text-white hover:bg-blue-900 disabled:opacity-60"
        >
          {mutation.isPending ? 'Booking...' : mutation.isError && !slotTaken ? 'Retry' : 'Book room'}
        </button>
      </div>
    </form>
  );
}

function ManagePanel({ state, onClose }: { state: Extract<ModalState, { kind: 'manage' }>; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { booking, zone } = state;
  const start = DateTime.fromISO(booking.start_time).setZone(zone);
  const end = DateTime.fromISO(booking.end_time).setZone(zone);

  const onSuccess = () => {
    void queryClient.invalidateQueries({ queryKey: ['availability'] });
    onClose();
  };
  const checkIn = useMutation({ mutationFn: () => api.checkIn(booking.id), onSuccess });
  const cancel = useMutation({ mutationFn: () => api.cancelBooking(booking.id), onSuccess });
  const busy = checkIn.isPending || cancel.isPending;
  const error = checkIn.error ?? cancel.error;

  const isConfirmed = booking.status === 'CONFIRMED';
  const statusClass = isConfirmed ? 'bg-blue-100 text-blue-800' : 'bg-emerald-100 text-emerald-800';

  return (
    <div className="space-y-4 px-5 py-4">
      <dl className="space-y-2 text-sm">
        <div className="text-slate-700">
          {start.toFormat('ccc, LLL d')}, {start.toFormat('HH:mm')}-{end.toFormat('HH:mm')}{' '}
          <span className="text-xs text-slate-400">({start.offsetNameShort})</span>
        </div>
        <div className="text-slate-700">
          Booked by {booking.organizer_name}
        </div>
        <div>
          <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${statusClass}`}>
            {booking.status.replace('_', ' ')}
          </span>
        </div>
      </dl>

      {error && (
        <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {errorMessage(error)}
        </p>
      )}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => cancel.mutate()}
          className="rounded-md border border-red-200 px-3 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-60"
        >
          {cancel.isPending ? 'Cancelling...' : 'Cancel booking'}
        </button>
        <button
          type="button"
          disabled={busy || !isConfirmed}
          onClick={() => checkIn.mutate()}
          className="rounded-md bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
        >
          {checkIn.isPending ? 'Checking in...' : isConfirmed ? 'Check in' : 'Checked in'}
        </button>
      </div>
    </div>
  );
}
