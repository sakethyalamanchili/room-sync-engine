import type {
  AvailabilityResponse,
  Booking,
  BookingCreateRequest,
  BookingUpdateRequest,
  ErrorBody,
  Room,
} from '../types/scheduling';

// In production both are built as "" so the app talks to whatever domain it was loaded from.
export const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';
export const WS_URL: string =
  import.meta.env.VITE_WS_URL ||
  (import.meta.env.VITE_WS_URL === ''
    ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/api/v1/ws`
    : 'ws://localhost:8000/api/v1/ws');

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
}

function formatDetail(body: ErrorBody | null, fallback: string): string {
  const detail = body?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((d) => {
        const field = d.loc.filter((p) => p !== 'body').join('.');
        return field ? `${field}: ${d.msg}` : d.msg;
      })
      .join('; ');
  }
  return fallback;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, 'Network error: the scheduling service is unreachable');
  }
  if (!response.ok) {
    let body: ErrorBody | null = null;
    try {
      body = (await response.json()) as ErrorBody;
    } catch {
      body = null;
    }
    throw new ApiError(response.status, formatDetail(body, response.statusText || 'Request failed'));
  }
  return (await response.json()) as T;
}

// crypto.randomUUID isn't available on plain http outside localhost, so fall back
export function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export const api = {
  getRooms: (): Promise<Room[]> => request<Room[]>('/api/v1/rooms'),

  getAvailability: (date: string): Promise<AvailabilityResponse> =>
    request<AvailabilityResponse>(`/api/v1/rooms/availability?date=${encodeURIComponent(date)}`),

  createBooking: (body: BookingCreateRequest, idempotencyKey: string): Promise<Booking> =>
    request<Booking>('/api/v1/bookings', {
      method: 'POST',
      body: JSON.stringify(body),
      headers: { 'Idempotency-Key': idempotencyKey },
    }),

  updateBooking: (id: string, body: BookingUpdateRequest): Promise<Booking> =>
    request<Booking>(`/api/v1/bookings/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),

  checkIn: (id: string): Promise<Booking> =>
    request<Booking>(`/api/v1/bookings/${id}/check-in`, { method: 'POST' }),

  cancelBooking: (id: string): Promise<Booking> =>
    request<Booking>(`/api/v1/bookings/${id}`, { method: 'DELETE' }),
};
