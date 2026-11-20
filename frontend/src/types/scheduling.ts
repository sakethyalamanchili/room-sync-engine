export type BookingStatus = 'CONFIRMED' | 'CHECKED_IN' | 'CANCELLED' | 'AUTO_RELEASED';

export interface Amenities {
  projector: boolean;
  whiteboard: boolean;
  video_conf: boolean;
}

export interface Room {
  id: string;
  name: string;
  floor: number;
  capacity: number;
  amenities: Amenities;
}

export interface AvailabilityBooking {
  id: string;
  title: string;
  organizer_name: string;
  start_time: string;
  end_time: string;
  status: BookingStatus;
}

export interface RoomAvailability extends Room {
  bookings: AvailabilityBooking[];
}

export interface BusinessHours {
  start: string;
  end: string;
  slot_minutes: number;
}

export interface AvailabilityResponse {
  date: string;
  timezone: string;
  business_hours: BusinessHours;
  rooms: RoomAvailability[];
}

export interface Booking {
  id: string;
  room_id: string;
  organizer_name: string;
  organizer_email: string;
  title: string;
  start_time: string;
  end_time: string;
  status: BookingStatus;
  created_at: string;
  updated_at: string;
}

export interface BookingCreateRequest {
  room_id: string;
  organizer_name: string;
  organizer_email: string;
  title: string;
  start_time: string;
  end_time: string;
}

export interface BookingUpdateRequest {
  title?: string;
  room_id?: string;
  start_time?: string;
  end_time?: string;
}

export type SlotAction = 'created' | 'updated' | 'cancelled' | 'checked_in' | 'auto_released';

export interface SlotUpdatedEvent {
  event: 'SLOT_UPDATED';
  action: SlotAction;
  date: string;
  room_id: string;
  room_name: string;
  booking_id: string;
}

export type SyncStatus = 'connecting' | 'live' | 'reconnecting' | 'offline';

export interface Toast {
  id: number;
  message: string;
}

// fastapi sends either a string or a list of validation errors
export interface ValidationErrorItem {
  loc: (string | number)[];
  msg: string;
  type: string;
}

export interface ErrorBody {
  detail?: string | ValidationErrorItem[];
}
