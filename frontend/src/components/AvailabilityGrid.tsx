import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { DateTime } from 'luxon';
import { ChevronLeft, ChevronRight, PenLine, Projector, Users, Video } from 'lucide-react';
import { api } from '../api/client';
import type { AvailabilityBooking, AvailabilityResponse, RoomAvailability, SyncStatus } from '../types/scheduling';
import BookingModal, { type ModalState } from './BookingModal';
import { LiveStatusBadge } from './LiveStatusBanner';

// only used for the initial date, after that we use the tz the API sends back
export const DEFAULT_ZONE = 'America/New_York';
const PAST_TOLERANCE_MINUTES = 10;

interface GridCell {
  key: string;
  start: number;
  span: number;
  booking?: AvailabilityBooking;
}

function buildCells(room: RoomAvailability, dayStart: DateTime, slotMinutes: number, slotCount: number): GridCell[] {
  const placed = room.bookings
    .map((b) => {
      const s = DateTime.fromISO(b.start_time).diff(dayStart, 'minutes').minutes / slotMinutes;
      const e = DateTime.fromISO(b.end_time).diff(dayStart, 'minutes').minutes / slotMinutes;
      const startIdx = Math.max(0, Math.floor(s));
      const endIdx = Math.min(slotCount, Math.ceil(e));
      return { booking: b, startIdx, endIdx };
    })
    .filter((p) => p.endIdx > p.startIdx)
    .sort((a, b) => a.startIdx - b.startIdx);

  const cells: GridCell[] = [];
  let i = 0;
  for (const p of placed) {
    if (p.startIdx < i) continue; // shouldn't happen, db prevents overlaps
    for (; i < p.startIdx; i++) cells.push({ key: `slot-${i}`, start: i, span: 1 });
    cells.push({ key: p.booking.id, start: i, span: p.endIdx - p.startIdx, booking: p.booking });
    i = p.endIdx;
  }
  for (; i < slotCount; i++) cells.push({ key: `slot-${i}`, start: i, span: 1 });
  return cells;
}

function AmenityBadges({ room }: { room: RoomAvailability }) {
  const items: { on: boolean; icon: ReactNode; label: string }[] = [
    { on: room.amenities.projector, icon: <Projector className="h-3 w-3" aria-hidden />, label: 'Projector' },
    { on: room.amenities.whiteboard, icon: <PenLine className="h-3 w-3" aria-hidden />, label: 'Whiteboard' },
    { on: room.amenities.video_conf, icon: <Video className="h-3 w-3" aria-hidden />, label: 'Video conferencing' },
  ];
  return (
    <div className="mt-1 flex gap-1">
      {items
        .filter((a) => a.on)
        .map((a) => (
          <span
            key={a.label}
            title={a.label}
            aria-label={a.label}
            className="inline-flex items-center rounded bg-slate-100 px-1.5 py-0.5 text-slate-500"
          >
            {a.icon}
          </span>
        ))}
    </div>
  );
}

export default function AvailabilityGrid({ syncStatus }: { syncStatus: SyncStatus }) {
  const [zone, setZone] = useState(DEFAULT_ZONE);
  const [date, setDate] = useState(() => DateTime.now().setZone(DEFAULT_ZONE).toISODate() ?? '');
  const [now, setNow] = useState(() => DateTime.now());
  const [modal, setModal] = useState<ModalState | null>(null);

  useEffect(() => {
    const t = window.setInterval(() => setNow(DateTime.now()), 30_000);
    return () => window.clearInterval(t);
  }, []);

  const query = useQuery<AvailabilityResponse>({
    queryKey: ['availability', date],
    queryFn: () => api.getAvailability(date),
    staleTime: 30_000,
    placeholderData: (prev) => prev,
  });
  const data = query.data;

  useEffect(() => {
    if (data?.timezone && data.timezone !== zone) setZone(data.timezone);
  }, [data?.timezone, zone]);

  const layout = useMemo(() => {
    if (!data) return null;
    const tz = data.timezone;
    const dayStart = DateTime.fromISO(`${data.date}T${data.business_hours.start}`, { zone: tz });
    const dayEnd = DateTime.fromISO(`${data.date}T${data.business_hours.end}`, { zone: tz });
    const slotMinutes = data.business_hours.slot_minutes;
    const slotCount = Math.round(dayEnd.diff(dayStart, 'minutes').minutes / slotMinutes);
    const slots = Array.from({ length: slotCount }, (_, i) => dayStart.plus({ minutes: i * slotMinutes }));
    return { tz, dayStart, dayEnd, slotMinutes, slotCount, slots };
  }, [data]);

  const shiftDay = useCallback(
    (days: number) => {
      const next = DateTime.fromISO(date, { zone }).plus({ days }).toISODate();
      if (next) setDate(next);
    },
    [date, zone],
  );

  const today = now.setZone(zone).toISODate();
  const cutoff = now.minus({ minutes: PAST_TOLERANCE_MINUTES });

  return (
    <section className="rounded-lg border border-slate-200 bg-white">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => shiftDay(-1)}
            className="rounded-md border border-slate-200 p-1.5 text-slate-600 hover:bg-slate-50"
            aria-label="Previous day"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <label className="rounded-md border border-slate-200 px-2 py-1 text-sm">
            <span className="sr-only">Date</span>
            <input
              type="date"
              value={date}
              onChange={(e) => e.target.value && setDate(e.target.value)}
              className="border-none bg-transparent text-sm text-slate-700 focus:outline-none"
            />
          </label>
          <button
            type="button"
            onClick={() => shiftDay(1)}
            className="rounded-md border border-slate-200 p-1.5 text-slate-600 hover:bg-slate-50"
            aria-label="Next day"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
          {date !== today && (
            <button
              type="button"
              onClick={() => today && setDate(today)}
              className="rounded-md px-2 py-1 text-xs font-medium text-blue-600 hover:bg-blue-50"
            >
              Today
            </button>
          )}
          {query.isFetching && <span className="text-xs text-slate-400">updating...</span>}
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-500">Times in {zone}</span>
          <LiveStatusBadge status={syncStatus} />
        </div>
      </header>

      {query.isError && (
        <div role="alert" className="m-4 flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          Could not load availability: {query.error instanceof Error ? query.error.message : 'unknown error'}
          <button type="button" onClick={() => void query.refetch()} className="ml-auto font-medium underline">
            Retry
          </button>
        </div>
      )}

      {!data || !layout ? (
        !query.isError && (
          <div className="py-20 text-center text-sm text-slate-500">
            Loading rooms...
          </div>
        )
      ) : (
        <div className="overflow-x-auto">
          <div
            className="grid w-full text-xs [--room-col:9rem] sm:[--room-col:14rem]"
            style={{
              gridTemplateColumns: `var(--room-col) repeat(${layout.slotCount}, minmax(3.5rem, 1fr))`,
              minWidth: `calc(var(--room-col) + ${layout.slotCount} * 3.5rem)`,
            }}
            role="grid"
            aria-label={`Room availability for ${data.date}`}
          >
            <div className="sticky left-0 z-10 border-b border-r border-slate-200 bg-slate-50 px-3 py-2 font-semibold text-slate-600" role="columnheader">
              Room
            </div>
            {layout.slots.map((s) => (
              <div key={s.toMillis()} className="border-b border-slate-200 bg-slate-50 px-1 py-2 text-center font-medium text-slate-500" role="columnheader">
                {s.toFormat('HH:mm')}
              </div>
            ))}

            {data.rooms.map((room) => {
              const cells = buildCells(room, layout.dayStart, layout.slotMinutes, layout.slotCount);
              return (
                <div key={room.id} className="contents" role="row">
                  <div className="sticky left-0 z-10 border-b border-r border-slate-200 bg-white px-3 py-2" role="rowheader">
                    <div className="font-semibold text-slate-800">{room.name}</div>
                    <div className="flex items-center gap-2 text-slate-500">
                      <span>Floor {room.floor}</span>
                      <span className="inline-flex items-center gap-0.5">
                        <Users className="h-3 w-3" aria-hidden /> {room.capacity}
                      </span>
                    </div>
                    <AmenityBadges room={room} />
                  </div>
                  {cells.map((cell) => {
                    const slotStart = layout.slots[cell.start];
                    if (cell.booking) {
                      const b = cell.booking;
                      const checkedIn = b.status === 'CHECKED_IN';
                      const s = DateTime.fromISO(b.start_time).setZone(layout.tz);
                      const e = DateTime.fromISO(b.end_time).setZone(layout.tz);
                      return (
                        <div key={cell.key} className="border-b border-slate-200 p-0.5" style={{ gridColumn: `span ${cell.span}` }} role="gridcell">
                          <button
                            type="button"
                            onClick={() => setModal({ kind: 'manage', room, booking: b, zone: layout.tz })}
                            aria-label={`${b.title} by ${b.organizer_name}, ${s.toFormat('HH:mm')} to ${e.toFormat('HH:mm')}, ${b.status}`}
                            className={`flex h-full w-full flex-col items-start overflow-hidden rounded-md border-l-4 px-2 py-1 text-left transition hover:brightness-95 ${
                              checkedIn ? 'border-emerald-500 bg-emerald-50 text-emerald-900' : 'border-blue-500 bg-blue-50 text-blue-900'
                            }`}
                          >
                            <span className="w-full truncate font-semibold">{b.title}</span>
                            <span className="w-full truncate opacity-80">{b.organizer_name}</span>
                            <span className="text-[10px] font-medium uppercase tracking-wide opacity-70">
                              {checkedIn ? 'Checked in' : 'Confirmed'}
                            </span>
                          </button>
                        </div>
                      );
                    }
                    const disabled = slotStart < cutoff;
                    const label = `${room.name}, ${slotStart.toFormat('HH:mm')}`;
                    return (
                      <div key={cell.key} className="border-b border-l border-slate-100 p-0.5" role="gridcell">
                        <button
                          type="button"
                          disabled={disabled}
                          onClick={() => setModal({ kind: 'create', room, slotStart, dayEnd: layout.dayEnd })}
                          aria-label={disabled ? `${label}, unavailable (past)` : `Book ${label}`}
                          className={`flex h-full min-h-[3.5rem] w-full items-center justify-center rounded-md text-[11px] font-medium ${
                            disabled
                              ? 'cursor-not-allowed bg-slate-100'
                              : 'text-transparent hover:bg-blue-50 hover:text-blue-700 focus:bg-blue-50 focus:text-blue-700 focus:outline-none'
                          }`}
                        >
                          {!disabled && '+ Book'}
                        </button>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <footer className="flex flex-wrap items-center gap-4 border-t border-slate-100 px-4 py-2 text-xs text-slate-500">
        <span className="inline-flex items-center gap-1">
          <span className="h-3 w-3 rounded-sm border-l-4 border-blue-500 bg-blue-50" /> Confirmed
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="h-3 w-3 rounded-sm border-l-4 border-emerald-500 bg-emerald-50" /> Checked in
        </span>
        <span>Unchecked bookings auto-release 15 minutes after start.</span>
      </footer>

      {modal && <BookingModal state={modal} onClose={() => setModal(null)} />}
    </section>
  );
}
