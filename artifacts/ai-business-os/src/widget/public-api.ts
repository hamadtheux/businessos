import type { PublicChatbotCapability } from "../services/chatbot.ts";

export type PublicAppointmentType = {
  reference: string;
  name: string;
  description: string | null;
  duration_minutes: number;
};

export type PublicWidgetConfig = {
  widget_id: string;
  display_name: string;
  business_name: string;
  welcome_message: string;
  placeholder_text: string;
  primary_color: string;
  logo_url: string | null;
  tone: string;
  theme: string;
  position: string;
  launcher_style: string;
  border_radius: number;
  locale: string;
  capabilities: PublicChatbotCapability[];
  privacy_policy_url: string | null;
  consent_text: string | null;
  require_lead_consent: boolean;
  appointment_types: PublicAppointmentType[];
};

export type PublicSession = { session_token: string; expires_at: string; locale: string };
export type ProductCard = { reference: string; item_type: "product" | "service"; name: string; description: string | null; price: string | null; currency: string };
export type ChatResponse = { message: string; suggested_actions: PublicChatbotCapability[]; products: ProductCard[]; handoff_status: "none" | "requested"; lead_capture_requested: boolean };
export type AvailabilitySlot = { slot_reference: string; appointment_type_reference: string; provider_reference: string; provider_display_name: string; starts_at: string; ends_at: string; timezone: string; location_reference: string | null };

type PublicApiOptions = { baseUrl: string; widgetId: string; token: string };

export class PublicWidgetApi {
  constructor(private readonly options: PublicApiOptions) {}

  message(message: string) {
    return this.request<ChatResponse>("/messages", { message });
  }

  lead(data: { name: string; email: string | null; phone: string | null; message: string | null; consent: boolean }) {
    return this.request<{ captured: true; message: string }>("/lead", data);
  }

  handoff() {
    return this.request<{ status: "requested"; message: string }>("/handoff", { reason: "visitor_requested" });
  }

  orderStatus(data: { order_reference: string; email: string | null; phone: string | null }) {
    return this.request<{ order_reference: string; status: string }>("/order-status", data);
  }

  availability(data: { appointment_type_reference: string; window_start: string; window_end: string; desired_results: number }) {
    return this.request<{ slots: AvailabilitySlot[] }>("/availability", data);
  }

  appointment(data: { slot_reference: string; appointment_type_reference: string; provider_reference: string; starts_at: string; name: string; email: string | null; phone: string | null; consent: boolean }) {
    return this.request<{ booked: true; status: "confirmed"; starts_at: string; ends_at: string; provider_display_name: string; appointment_type_name: string }>("/appointments", data);
  }

  private async request<T>(tail: string, body: unknown): Promise<T> {
    const response = await fetch(
      `${this.options.baseUrl}/api/v1/public/widgets/${encodeURIComponent(this.options.widgetId)}/sessions${tail}`,
      {
        method: "POST",
        credentials: "omit",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${this.options.token}` },
        body: JSON.stringify(body),
      },
    );
    if (!response.ok) {
      const value = await response.json().catch(() => null) as {
        detail?: string | { code?: string; message?: string };
      } | null;
      const detail = value?.detail;
      const message = typeof detail === "string" ? detail : detail?.message;
      throw new Error(message || "This request could not be completed.");
    }
    return response.json() as Promise<T>;
  }
}
