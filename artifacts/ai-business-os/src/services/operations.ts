import { apiClient, type ApiClient } from "./api-client.ts";
import type {
  AuditLog, BusinessReport, Conversation, ConversationMessage, CoreAnalytics,
  Customer, CustomerCreate, CustomerUpdate, Lead, LeadCreate, LeadStage,
  LeadUpdate, Notification, Opportunity, OpportunityCreate, OpportunityStatus,
  Order, OrderCreate, OrderStatus, PageResponse,
} from "./api-types.ts";

type ListOptions = { page?: number; pageSize?: number; search?: string; status?: string };

const businessPath = (businessId: string, path: string) =>
  `/api/v1/businesses/${encodeURIComponent(businessId)}${path}`;

function query(options: ListOptions = {}) {
  const params = new URLSearchParams({
    page: String(options.page ?? 1),
    page_size: String(options.pageSize ?? 25),
  });
  if (options.search) params.set("search", options.search);
  if (options.status) params.set("status", options.status);
  return params.toString();
}

export function createOperationsApi(client: ApiClient) {
 return {
  customers: {
    list: (id: string, options?: ListOptions, signal?: AbortSignal) => client.request<PageResponse<Customer>>(businessPath(id, `/customers?${query(options)}`), { signal }),
    get: (id: string, customerId: string, signal?: AbortSignal) => client.request<Customer>(businessPath(id, `/customers/${customerId}`), { signal }),
    create: (id: string, data: CustomerCreate) => client.request<Customer>(businessPath(id, "/customers"), { method: "POST", json: data }),
    update: (id: string, customerId: string, data: CustomerUpdate) => client.request<Customer>(businessPath(id, `/customers/${customerId}`), { method: "PATCH", json: data }),
  },
  leads: {
    list: (id: string, options?: ListOptions, signal?: AbortSignal) => client.request<PageResponse<Lead>>(businessPath(id, `/crm/leads?${query(options)}`), { signal }),
    create: (id: string, data: LeadCreate) => client.request<Lead>(businessPath(id, "/crm/leads"), { method: "POST", json: data }),
    update: (id: string, leadId: string, data: LeadUpdate) => client.request<Lead>(businessPath(id, `/crm/leads/${leadId}`), { method: "PATCH", json: data }),
    stage: (id: string, leadId: string, stage: LeadStage) => client.request<Lead>(businessPath(id, `/crm/leads/${leadId}/stage`), { method: "POST", json: { stage } }),
    qualify: (id: string, leadId: string, qualification_state: Lead["qualification_state"]) => client.request<Lead>(businessPath(id, `/crm/leads/${leadId}/qualification`), { method: "POST", json: { qualification_state } }),
  },
  orders: {
    list: (id: string, options?: ListOptions, signal?: AbortSignal) => client.request<PageResponse<Order>>(businessPath(id, `/orders?${query(options)}`), { signal }),
    create: (id: string, data: OrderCreate) => client.request<Order>(businessPath(id, "/orders"), { method: "POST", json: data }),
    status: (id: string, orderId: string, status: OrderStatus) => client.request<Order>(businessPath(id, `/orders/${orderId}/status`), { method: "POST", json: { status } }),
  },
  conversations: {
    list: (id: string, options?: ListOptions, signal?: AbortSignal) => client.request<PageResponse<Conversation>>(businessPath(id, `/conversations?${query(options)}`), { signal }),
    get: (id: string, conversationId: string, signal?: AbortSignal) => client.request<Conversation>(businessPath(id, `/conversations/${conversationId}`), { signal }),
    create: (id: string, data: { customer_id?: string | null; channel: Conversation["channel"]; external_reference?: string | null; assigned_user_id?: string | null }) => client.request<Conversation>(businessPath(id, "/conversations"), { method: "POST", json: data }),
    update: (id: string, conversationId: string, data: { status?: Conversation["status"]; assigned_user_id?: string | null }) => client.request<Conversation>(businessPath(id, `/conversations/${conversationId}`), { method: "PATCH", json: data }),
    message: (id: string, conversationId: string, content: string) => client.request<ConversationMessage>(businessPath(id, `/conversations/${conversationId}/messages`), { method: "POST", json: { direction: "internal", content } }),
  },
  notifications: {
    list: (id: string, unreadOnly = false, signal?: AbortSignal) => client.request<PageResponse<Notification>>(businessPath(id, `/notifications?page=1&page_size=100&unread_only=${unreadOnly}`), { signal }),
    read: (id: string, notificationId: string) => client.request<Notification>(businessPath(id, `/notifications/${notificationId}/read`), { method: "POST" }),
    readAll: (id: string) => client.request<{ updated: number }>(businessPath(id, "/notifications/read-all"), { method: "POST" }),
  },
  opportunities: {
    list: (id: string, options?: ListOptions, signal?: AbortSignal) => client.request<PageResponse<Opportunity>>(businessPath(id, `/opportunities?${query(options)}`), { signal }),
    create: (id: string, data: OpportunityCreate) => client.request<Opportunity>(businessPath(id, "/opportunities"), { method: "POST", json: data }),
    status: (id: string, opportunityId: string, status: OpportunityStatus) => client.request<Opportunity>(businessPath(id, `/opportunities/${opportunityId}/status`), { method: "POST", json: { status } }),
  },
  audit: (id: string, options?: ListOptions, signal?: AbortSignal) => client.request<PageResponse<AuditLog>>(businessPath(id, `/audit?${query(options)}`), { signal }),
  analytics: (id: string, start: string, end: string, signal?: AbortSignal) => client.request<CoreAnalytics>(businessPath(id, `/analytics/core?period_start=${start}&period_end=${end}`), { signal }),
  reports: {
    list: (id: string, signal?: AbortSignal) => client.request<PageResponse<BusinessReport>>(businessPath(id, "/reports?page=1&page_size=25"), { signal }),
    generate: (id: string, data: { report_type: BusinessReport["report_type"]; period_start: string; period_end: string }) => client.request<BusinessReport>(businessPath(id, "/reports/generate"), { method: "POST", json: data }),
  },
 };
}

export const operationsApi = createOperationsApi(apiClient);
