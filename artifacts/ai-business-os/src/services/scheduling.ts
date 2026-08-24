import { apiClient } from "./api-client.ts";
import type { Appointment, AppointmentType, AvailabilityException, AvailabilityRule, AvailabilitySlot, ProviderAppointmentType, ServiceProvider } from "./api-types.ts";

const path = (id: string, tail: string) => `/api/v1/businesses/${encodeURIComponent(id)}/scheduling${tail}`;

export const schedulingApi = {
  providers: (id: string, signal?: AbortSignal) => apiClient.request<ServiceProvider[]>(path(id, "/providers?active=true"), { signal }),
  allProviders: (id: string, signal?: AbortSignal) => apiClient.request<ServiceProvider[]>(path(id, "/providers"), { signal }),
  createProvider: (id: string, data: Omit<ServiceProvider, "id" | "business_id" | "created_at" | "updated_at">) => apiClient.request<ServiceProvider>(path(id, "/providers"), { method: "POST", json: data }),
  updateProvider: (id: string, providerId: string, data: Partial<Omit<ServiceProvider, "id" | "business_id" | "created_at" | "updated_at">>) => apiClient.request<ServiceProvider>(path(id, `/providers/${providerId}`), { method: "PATCH", json: data }),
  appointmentTypes: (id: string, signal?: AbortSignal) => apiClient.request<AppointmentType[]>(path(id, "/appointment-types?active=true"), { signal }),
  allAppointmentTypes: (id: string, signal?: AbortSignal) => apiClient.request<AppointmentType[]>(path(id, "/appointment-types"), { signal }),
  createAppointmentType: (id: string, data: Omit<AppointmentType, "id" | "business_id" | "created_at" | "updated_at">) => apiClient.request<AppointmentType>(path(id, "/appointment-types"), { method: "POST", json: data }),
  updateAppointmentType: (id: string, typeId: string, data: Partial<Omit<AppointmentType, "id" | "business_id" | "created_at" | "updated_at">>) => apiClient.request<AppointmentType>(path(id, `/appointment-types/${typeId}`), { method: "PATCH", json: data }),
  providerTypes: (id: string, providerId: string, signal?: AbortSignal) => apiClient.request<ProviderAppointmentType[]>(path(id, `/providers/${providerId}/appointment-types`), { signal }),
  assignProviderType: (id: string, providerId: string, typeId: string) => apiClient.request<ProviderAppointmentType>(path(id, `/providers/${providerId}/appointment-types/${typeId}`), { method: "PUT" }),
  unassignProviderType: (id: string, providerId: string, typeId: string) => apiClient.request<null>(path(id, `/providers/${providerId}/appointment-types/${typeId}`), { method: "DELETE" }),
  rules: (id: string, providerId: string, signal?: AbortSignal) => apiClient.request<AvailabilityRule[]>(path(id, `/providers/${providerId}/availability-rules`), { signal }),
  createRule: (id: string, providerId: string, data: { weekday: number; start_local_time: string; end_local_time: string; valid_from?: string | null; valid_until?: string | null; active?: boolean }) => apiClient.request<AvailabilityRule>(path(id, `/providers/${providerId}/availability-rules`), { method: "POST", json: data }),
  updateRule: (id: string, ruleId: string, data: Partial<AvailabilityRule>) => apiClient.request<AvailabilityRule>(path(id, `/availability-rules/${ruleId}`), { method: "PATCH", json: data }),
  deleteRule: (id: string, ruleId: string) => apiClient.request<null>(path(id, `/availability-rules/${ruleId}`), { method: "DELETE" }),
  exceptions: (id: string, providerId: string, signal?: AbortSignal) => apiClient.request<AvailabilityException[]>(path(id, `/providers/${providerId}/availability-exceptions`), { signal }),
  createException: (id: string, providerId: string, data: { exception_date: string; exception_kind: AvailabilityException["exception_kind"]; whole_day: boolean; start_local_time?: string | null; end_local_time?: string | null; active?: boolean }) => apiClient.request<AvailabilityException>(path(id, `/providers/${providerId}/availability-exceptions`), { method: "POST", json: data }),
  updateException: (id: string, exceptionId: string, data: Partial<AvailabilityException>) => apiClient.request<AvailabilityException>(path(id, `/availability-exceptions/${exceptionId}`), { method: "PATCH", json: data }),
  deleteException: (id: string, exceptionId: string) => apiClient.request<null>(path(id, `/availability-exceptions/${exceptionId}`), { method: "DELETE" }),
  appointments: (id: string, start: string, end: string, signal?: AbortSignal) => apiClient.request<Appointment[]>(path(id, `/appointments?window_start=${encodeURIComponent(start)}&window_end=${encodeURIComponent(end)}&limit=500`), { signal }),
  availability: (id: string, data: { appointment_type_id: string; provider_id?: string; window_start: string; window_end: string; desired_results?: number }) => apiClient.request<{ slots: AvailabilitySlot[] }>(path(id, "/availability/search"), { method: "POST", json: data }),
  book: (id: string, data: { provider_id: string; appointment_type_id: string; customer_id?: string | null; starts_at: string; source?: "manual" }) => apiClient.request<Appointment>(path(id, "/appointments"), { method: "POST", json: data }),
  cancel: (id: string, appointmentId: string) => apiClient.request<Appointment>(path(id, `/appointments/${appointmentId}/cancel`), { method: "POST", json: { reason_code: "customer_request" } }),
  reschedule: (id: string, appointmentId: string, startsAt: string) => apiClient.request<Appointment>(path(id, `/appointments/${appointmentId}/reschedule`), { method: "POST", json: { starts_at: startsAt } }),
};
