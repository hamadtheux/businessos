export type UserStatus = "active" | "inactive" | "suspended";

export type CatalogItemType = "product" | "service";

export type CatalogItemStatus = "active" | "draft" | "archived";

export type CatalogItem = {
  id: string;
  business_id: string;
  item_type: CatalogItemType;
  name: string;
  description: string | null;
  sku: string | null;
  /** Exact JSON decimal representation returned by the API. */
  price: string | null;
  compare_at_price: string | null;
  currency: string | null;
  cost: string | null;
  product_url: string | null;
  inventory_quantity: number | null;
  availability:
    "unknown" | "in_stock" | "out_of_stock" | "preorder" | "backorder";
  brand: string | null;
  vendor: string | null;
  gtin: string | null;
  mpn: string | null;
  condition: "new" | "refurbished" | "used";
  google_product_category: string | null;
  tags: string[];
  published: boolean;
  source: string;
  sync_state:
    | "manual"
    | "in_sync"
    | "pending"
    | "local_override"
    | "external_changed"
    | "error";
  last_synchronized_at: string | null;
  provider_metadata: Record<string, unknown>;
  status: CatalogItemStatus;
  created_at: string;
  updated_at: string;
};

export type CatalogItemCreate = {
  item_type: CatalogItemType;
  name: string;
  description?: string | null;
  sku?: string | null;
  /** Decimal text is sent unchanged; blank prices are represented by null. */
  price?: string | null;
  compare_at_price?: string | null;
  currency?: string | null;
  cost?: string | null;
  product_url?: string | null;
  inventory_quantity?: number | null;
  availability?: CatalogItem["availability"];
  brand?: string | null;
  vendor?: string | null;
  gtin?: string | null;
  mpn?: string | null;
  condition?: CatalogItem["condition"];
  google_product_category?: string | null;
  tags?: string[];
  published?: boolean;
  status?: CatalogItemStatus;
};

export type CatalogItemUpdate = {
  item_type?: CatalogItemType;
  name?: string;
  description?: string | null;
  sku?: string | null;
  price?: string | null;
  compare_at_price?: string | null;
  currency?: string | null;
  cost?: string | null;
  product_url?: string | null;
  inventory_quantity?: number | null;
  availability?: CatalogItem["availability"];
  brand?: string | null;
  vendor?: string | null;
  gtin?: string | null;
  mpn?: string | null;
  condition?: CatalogItem["condition"];
  google_product_category?: string | null;
  tags?: string[];
  published?: boolean;
  status?: CatalogItemStatus;
};

export type BusinessKnowledgeCategory =
  | "general"
  | "faq"
  | "policy"
  | "procedure"
  | "brand"
  | "sales"
  | "support"
  | "operations"
  | "marketing";

export type BusinessKnowledgeStatus = "active" | "draft" | "archived";

export type BusinessKnowledgeSourceType = "manual" | "system";

export type BusinessKnowledgeEntry = {
  id: string;
  business_id: string;
  category: BusinessKnowledgeCategory;
  title: string;
  content: string;
  status: BusinessKnowledgeStatus;
  source_type: BusinessKnowledgeSourceType;
  source_reference: string | null;
  created_at: string;
  updated_at: string;
};

export type BusinessKnowledgeEntryCreate = {
  category: BusinessKnowledgeCategory;
  title: string;
  content: string;
  status: BusinessKnowledgeStatus;
};

export type BusinessKnowledgeEntryUpdate = {
  category?: BusinessKnowledgeCategory;
  title?: string;
  content?: string;
  status?: BusinessKnowledgeStatus;
};

export type BusinessBrainSourceType =
  | "business_profile"
  | "branding"
  | "appointment_type"
  | "catalog_item"
  | "knowledge_entry";

export type BusinessBrainManifest = {
  business_id: string;
  source_count: number;
  source_counts_by_type: Record<BusinessBrainSourceType, number>;
  revision: string;
};

export type CatalogImportField =
  "name" | "item_type" | "description" | "sku" | "price" | "status";

export type DetectedColumnMapping = Partial<Record<CatalogImportField, string>>;

export type CatalogImportRowError = {
  row: number;
  field: string | null;
  message: string;
};

export type CatalogImportPreviewRow = {
  row: number;
  normalized: Record<CatalogImportField, string | null>;
  item: CatalogItemCreate | null;
  errors: CatalogImportRowError[];
};

export type CatalogImportPreviewResponse = {
  file: {
    filename: string;
    file_type: "csv" | "xlsx";
    size_bytes: number;
  };
  detected_columns: DetectedColumnMapping;
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  preview_rows: CatalogImportPreviewRow[];
  errors: CatalogImportRowError[];
  preview_limit: number;
};

export type CatalogImportResult = {
  created_count: number;
  total_rows: number;
};

export type UserPublic = {
  id: string;
  email: string;
  first_name: string;
  last_name: string | null;
  status: UserStatus;
  is_email_verified: boolean;
  created_at: string;
};

export type UserLoginResponse = {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: UserPublic;
};

export type UserLoginInput = {
  email: string;
  password: string;
};

export type UserRegistrationInput = {
  email: string;
  password: string;
  first_name: string;
  last_name: string | null;
};

export type ApiValidationIssue = {
  loc: Array<string | number>;
  msg: string;
  type: string;
};

export type ApiErrorPayload = {
  detail?:
    | string
    | ApiValidationIssue[]
    | {
        code: "feature_not_in_plan" | "usage_limit_reached" | string;
        message?: string;
        entitlement_key?: string;
        current?: number | null;
        limit?: number | null;
        upgrade_required?: boolean;
      };
  file?: CatalogImportPreviewResponse["file"];
  detected_columns?: DetectedColumnMapping;
  total_rows?: number;
  valid_rows?: number;
  invalid_rows?: number;
  preview_rows?: CatalogImportPreviewRow[];
  errors?: CatalogImportRowError[];
  preview_limit?: number;
};

export type BusinessStatus = "active" | "inactive" | "suspended";

export type BusinessSummary = {
  id: string;
  name: string;
  slug: string;
  business_type: string;
  status: BusinessStatus;
  timezone: string;
  currency: string;
  locale: string;
  website_url?: string | null;
  location?: string | null;
  description?: string | null;
  brand_voice?: string | null;
  avoid_keywords?: string[];
  membership_role: string;
  created_at: string;
};

export type BusinessProfileUpdate = {
  name: string;
  timezone: string;
  currency: string;
  locale: string;
  website_url: string | null;
  location: string | null;
  description: string | null;
  brand_voice: string | null;
  avoid_keywords: string[];
};

export type BusinessBrandingInput = {
  primary_color?: string;
  secondary_color?: string;
  accent_color?: string;
};

export type BusinessBrandingResponse = {
  primary_color: string | null;
  secondary_color: string | null;
  accent_color: string | null;
  logo_url: string | null;
};

export type BusinessBrandingUpdate = {
  primary_color: string | null;
  secondary_color: string | null;
  accent_color: string | null;
};

export type BusinessOnboardingInput = {
  business_id: string;
  name: string;
  business_type: string;
  timezone: string;
  currency: string;
  locale: string;
  website_url?: string | null;
  location?: string | null;
  description?: string | null;
  brand_voice?: string | null;
  avoid_keywords?: string[];
  branding?: BusinessBrandingInput;
};

export type BusinessOnboardingResponse = {
  business: BusinessSummary;
  branding: BusinessBrandingResponse | null;
  created: boolean;
};

export type PageResponse<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};

export type CustomerStatus = "active" | "inactive" | "archived";
export type Customer = {
  id: string;
  business_id: string;
  display_name: string;
  first_name: string | null;
  last_name: string | null;
  email: string | null;
  phone: string | null;
  status: CustomerStatus;
  source: string;
  tags: string[];
  company: string | null;
  notes: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
};
export type CustomerCreate = Pick<Customer, "display_name"> &
  Partial<
    Pick<
      Customer,
      | "first_name"
      | "last_name"
      | "email"
      | "phone"
      | "status"
      | "source"
      | "tags"
      | "company"
      | "notes"
    >
  >;
export type CustomerUpdate = Partial<CustomerCreate>;

export type LeadStage =
  "new" | "qualified" | "contacted" | "viewing" | "proposal" | "won" | "lost";
export type Lead = {
  id: string;
  business_id: string;
  customer_id: string | null;
  owner_user_id: string | null;
  display_name: string;
  company: string | null;
  email: string | null;
  phone: string | null;
  stage: LeadStage;
  source: string;
  priority: "low" | "medium" | "high" | "urgent";
  qualification_state:
    "unqualified" | "qualifying" | "qualified" | "disqualified";
  estimated_value: string | null;
  currency: string;
  expected_close_date: string | null;
  next_follow_up_at: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
};
export type LeadCreate = Omit<
  Lead,
  "id" | "business_id" | "created_at" | "updated_at"
>;
export type LeadUpdate = Partial<
  Omit<LeadCreate, "stage" | "qualification_state">
>;

export type OrderStatus =
  "draft" | "confirmed" | "processing" | "completed" | "canceled";
export type OrderLine = {
  id: string;
  business_id: string;
  order_id: string;
  catalog_item_id: string | null;
  description: string;
  quantity: number;
  unit_price: string;
  created_at: string;
  updated_at: string;
};
export type Order = {
  id: string;
  business_id: string;
  customer_id: string;
  customer_display_name: string;
  order_number: string;
  status: OrderStatus;
  source: string;
  currency: string;
  subtotal: string;
  adjustment_amount: string;
  total: string;
  notes: string | null;
  lines: OrderLine[];
  created_at: string;
  updated_at: string;
};
export type OrderCreate = {
  customer_id: string;
  source?: string;
  currency: string;
  adjustment_amount?: string;
  notes?: string | null;
  lines: Array<{
    catalog_item_id?: string | null;
    description: string;
    quantity: number;
    unit_price: string;
  }>;
};

export type ConversationStatus = "open" | "escalated" | "resolved";
export type ConversationChannel =
  | "website"
  | "whatsapp"
  | "email"
  | "facebook"
  | "instagram"
  | "manual"
  | "other";
export type ConversationMessage = {
  id: string;
  business_id: string;
  conversation_id: string;
  direction: "inbound" | "outbound" | "internal";
  sender_type: "customer" | "user" | "ai" | "system";
  sender_user_id: string | null;
  content: string;
  sent_at: string;
  external_reference: string | null;
  delivery_status:
    | "received"
    | "recorded"
    | "submitted"
    | "sent"
    | "delivered"
    | "read"
    | "failed";
  action_execution_attempt_id: string | null;
  created_at: string;
  updated_at: string;
};
export type Conversation = {
  id: string;
  business_id: string;
  customer_id: string | null;
  integration_connection_id: string | null;
  customer_display_name: string | null;
  channel: ConversationChannel;
  external_reference: string | null;
  status: ConversationStatus;
  assigned_user_id: string | null;
  last_activity_at: string;
  latest_message: string | null;
  unread: boolean;
  messages: ConversationMessage[];
  created_at: string;
  updated_at: string;
};

export type Notification = {
  id: string;
  business_id: string;
  recipient_user_id: string | null;
  category: string;
  title: string;
  message: string;
  priority: "low" | "medium" | "high";
  related_entity_type: string | null;
  related_entity_id: string | null;
  read: boolean;
  created_at: string;
  updated_at: string;
};

export type OpportunityStatus =
  "open" | "in_progress" | "won" | "lost" | "dismissed";
export type Opportunity = {
  id: string;
  business_id: string;
  title: string;
  description: string;
  category: string;
  source: string;
  priority: "low" | "medium" | "high" | "urgent";
  estimated_value: string | null;
  currency: string | null;
  status: OpportunityStatus;
  customer_id: string | null;
  lead_id: string | null;
  created_at: string;
  updated_at: string;
};
export type OpportunityCreate = Omit<
  Opportunity,
  "id" | "business_id" | "created_at" | "updated_at"
>;

export type AuditLog = {
  id: string;
  business_id: string;
  actor_user_id: string | null;
  actor_type: "user" | "ai" | "system";
  event_type: string;
  entity_type: string;
  entity_id: string | null;
  summary: string;
  before_value: string | null;
  after_value: string | null;
  status: "completed" | "failed" | "pending";
  created_at: string;
};

export type AnalyticsPoint = { label: string; revenue: string; orders: number };
export type CoreAnalytics = {
  period_start: string;
  period_end: string;
  customers: number;
  leads: number;
  crm_stage_counts: Record<string, number>;
  orders: number;
  order_revenue: string;
  average_order_value: string;
  appointments: number;
  appointment_status_counts: Record<string, number>;
  providers: number;
  opportunities: number;
  opportunity_status_counts: Record<string, number>;
  ai_executions: number;
  ai_actions: number;
  revenue_series: AnalyticsPoint[];
  lead_source_counts: Record<string, number>;
};

export type ReportMetricValue =
  | string
  | number
  | Record<string, number>
  | Array<Record<string, string | number>>;
export type BusinessReport = {
  id: string;
  business_id: string;
  report_type:
    "daily_operations" | "sales" | "customer" | "scheduling" | "marketing";
  period_start: string;
  period_end: string;
  status: "ready" | "failed";
  generated_at: string;
  summary: string;
  metrics: Record<string, ReportMetricValue>;
  created_at: string;
  updated_at: string;
};

export type ServiceProvider = {
  id: string;
  business_id: string;
  display_name: string;
  provider_type: string;
  title: string | null;
  specialty: string | null;
  active: boolean;
  timezone: string;
  location_reference: string | null;
  created_at: string;
  updated_at: string;
};
export type AppointmentType = {
  id: string;
  business_id: string;
  name: string;
  description: string | null;
  duration_minutes: number;
  buffer_before_minutes: number;
  buffer_after_minutes: number;
  slot_interval_minutes: number;
  active: boolean;
  minimum_notice_minutes: number;
  maximum_future_days: number;
  allow_same_day: boolean;
  cancellation_cutoff_minutes: number;
  reschedule_cutoff_minutes: number;
  created_at: string;
  updated_at: string;
};
export type Appointment = {
  id: string;
  business_id: string;
  provider_id: string;
  appointment_type_id: string;
  customer_id: string | null;
  starts_at: string;
  ends_at: string;
  status: "confirmed" | "canceled" | "completed" | "no_show";
  source: "manual" | "api" | "ai" | "website" | "whatsapp" | "import";
  created_by_user_id: string | null;
  cancellation_reason_code: string | null;
  created_at: string;
  updated_at: string;
};
export type AvailabilitySlot = {
  provider_id: string;
  provider_display_name: string;
  appointment_type_id: string;
  starts_at: string;
  ends_at: string;
  timezone: string;
  location_reference: string | null;
};
export type ProviderAppointmentType = {
  id: string;
  business_id: string;
  provider_id: string;
  appointment_type_id: string;
  created_at: string;
  updated_at: string;
};
export type AvailabilityRule = {
  id: string;
  business_id: string;
  provider_id: string;
  weekday: number;
  start_local_time: string;
  end_local_time: string;
  valid_from: string | null;
  valid_until: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
};
export type AvailabilityException = {
  id: string;
  business_id: string;
  provider_id: string;
  exception_date: string;
  exception_kind: "unavailable" | "available_override";
  whole_day: boolean;
  start_local_time: string | null;
  end_local_time: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
};

export type MarketingChannel =
  | "meta"
  | "google_ads"
  | "instagram"
  | "facebook"
  | "linkedin"
  | "tiktok"
  | "email"
  | "whatsapp"
  | "website"
  | "other";
export type MarketingPlanStatus =
  "draft" | "ready" | "active" | "completed" | "archived";
export type CampaignStatus =
  | "draft"
  | "planned"
  | "awaiting_approval"
  | "approved"
  | "executing"
  | "provider_pending"
  | "scheduled"
  | "active"
  | "paused"
  | "completed"
  | "canceled"
  | "failed"
  | "attention_required"
  | "unknown_external_state";
export type MarketingContentStatus =
  | "draft"
  | "review"
  | "approved"
  | "scheduled"
  | "ready_to_publish"
  | "archived";
export type MarketingContentType =
  | "social_post"
  | "ad_copy"
  | "email_draft"
  | "whatsapp_draft"
  | "blog_draft"
  | "landing_page_copy"
  | "headline"
  | "cta"
  | "content_package";
export type MarketingTrendStatus =
  "detected" | "reviewed" | "acted_on" | "dismissed" | "expired";

export type MarketingAudience = {
  id: string;
  business_id: string;
  name: string;
  countries: string[];
  regions: string[];
  min_age: number;
  max_age: number;
  languages: string[];
  customer_lifecycle: string[];
  crm_stages: string[];
  interests: string[];
  existing_customer_segment: string | null;
  segment_description: string | null;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
};

export type MarketingPlan = {
  id: string;
  business_id: string;
  audience_id: string | null;
  title: string;
  objective: string;
  target_audience: string;
  positioning: string;
  key_message: string;
  offer: string | null;
  channels: MarketingChannel[];
  budget_guidance: string | null;
  currency: string;
  period_start: string | null;
  period_end: string | null;
  content_strategy: string | null;
  measurement_goals: string[];
  status: MarketingPlanStatus;
  generated_by: "user" | "ai";
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
};

export type CampaignChannelPlan = {
  id: string;
  business_id: string;
  campaign_id: string;
  channel: MarketingChannel;
  objective: string;
  budget_allocation: string;
  audience_strategy: string;
  messaging: string;
  status:
    | "draft"
    | "ready"
    | "approved"
    | "scheduled"
    | "active"
    | "completed"
    | "archived";
  planned_start: string | null;
  planned_end: string | null;
  safe_configuration: {
    placements: string[];
    keywords: string[];
    call_to_action: string | null;
    destination_path: string | null;
    optimization_goal: string | null;
    notes: string | null;
  };
  created_at: string;
  updated_at: string;
};

export type MarketingCampaign = {
  id: string;
  business_id: string;
  marketing_plan_id: string | null;
  audience_id: string | null;
  name: string;
  objective: string;
  description: string | null;
  offer: string | null;
  audience_definition: string;
  geographic_targeting: string[];
  channels: MarketingChannel[];
  start_date: string | null;
  end_date: string | null;
  planned_budget: string;
  currency: string;
  budget_mode: "daily" | "lifetime";
  status: CampaignStatus;
  created_by_user_id: string | null;
  ai_generated: boolean;
  origin_type?: string | null;
  proposal_key?: string | null;
  proposal_reasoning?: string | null;
  creative_brief?: string | null;
  proposed_copy?: string | null;
  proposed_cta?: string | null;
  landing_destination?: string | null;
  measurement_plan?: string | null;
  assumptions?: string[] | null;
  risks?: string[] | null;
  required_integrations?: string[] | null;
  source_evidence?: Array<Record<string, unknown>> | null;
  normalized_proposal: Record<string, unknown>;
  recommended_provider: "google" | "meta" | null;
  campaign_type: "retail_performance_max" | "catalog_sales" | null;
  offer_source: "none" | "authoritative_promotion" | "owner_authorized";
  offer_authorized: boolean;
  proposal_confidence: string | null;
  audience_hypothesis_id?: string | null;
  catalog_item_ids: string[];
  created_at: string;
  updated_at: string;
  channel_plans?: CampaignChannelPlan[];
};

export type MarketingContent = {
  id: string;
  business_id: string;
  campaign_id: string | null;
  channel: MarketingChannel;
  content_type: MarketingContentType;
  title: string;
  body: string;
  cta: string | null;
  language: string;
  status: MarketingContentStatus;
  ai_generated: boolean;
  version: number;
  parent_content_id: string | null;
  root_content_id: string;
  created_by_user_id: string | null;
  proposal_key?: string | null;
  creative_brief?: string | null;
  recommended_for?: string | null;
  generation_reasoning?: string | null;
  source_evidence?: Array<Record<string, unknown>> | null;
  created_at: string;
  updated_at: string;
};

export type CreativeAsset = {
  id: string;
  business_id: string;
  campaign_id: string | null;
  content_id: string | null;
  asset_type: string;
  source_type: "manual" | "import" | "ai_brief" | "future_provider";
  instructions: string | null;
  visual_direction: string | null;
  generation_status:
    | "draft"
    | "brief_ready"
    | "provider_required"
    | "ready"
    | "failed"
    | "archived";
  storage_reference: string | null;
  width: number | null;
  height: number | null;
  aspect_ratio: string | null;
  alt_text: string | null;
  created_at: string;
  updated_at: string;
};

export type SocialSchedule = {
  id: string;
  business_id: string;
  content_id: string;
  campaign_id: string | null;
  channel: MarketingChannel;
  scheduled_for: string;
  timezone: string;
  status: "scheduled" | "unscheduled" | "canceled" | "ready_to_publish";
  created_at: string;
  updated_at: string;
};

export type MarketingCompetitor = {
  id: string;
  business_id: string;
  name: string;
  website_domain: string | null;
  description: string | null;
  active: boolean;
  notes: string | null;
  source_candidate_id?: string | null;
  confirmation_source?: string | null;
  created_at: string;
  updated_at: string;
};

export type CompetitorDiscoveryRun = {
  id: string;
  business_id: string;
  trigger_type: "onboarding" | "brain_change" | "scheduled" | "manual_refresh";
  provider_key: string | null;
  brain_revision: string | null;
  idempotency_key: string;
  status:
    | "queued"
    | "running"
    | "completed"
    | "provider_unavailable"
    | "blocked_entitlement"
    | "failed";
  candidate_count: number;
  results_processed: number;
  new_candidates: number;
  refreshed_candidates: number;
  evidence_added: number;
  failure_code: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type CompetitorDiscoveryStatus = {
  latest_run: CompetitorDiscoveryRun | null;
  provider_available: boolean;
  suggested_count: number;
  monitored_count: number;
  manual_refresh_available_at: string | null;
};

export type CompetitorCandidate = {
  id: string;
  business_id: string;
  discovery_run_id: string;
  competitor_id: string | null;
  name: string;
  website_domain: string | null;
  canonical_url: string | null;
  source: string;
  discovery_reason: string;
  confidence: string;
  industry_relationship: string | null;
  geographic_relationship: string | null;
  status: "suggested" | "confirmed" | "dismissed" | "monitoring";
  discovered_at: string;
  last_seen_at: string;
  created_at: string;
  updated_at: string;
};

export type CompetitorCandidateEvidence = {
  id: string;
  business_id: string;
  candidate_id: string;
  discovery_run_id: string;
  source_type:
    "provider_result" | "public_url" | "public_metadata" | "ai_inference";
  source_reference: string;
  title: string;
  excerpt: string;
  observed_at: string;
  safe_metadata: Record<string, unknown>;
  fingerprint: string;
  created_at: string;
  updated_at: string;
};

export type MarketingAutomationRun = {
  id: string;
  business_id: string;
  run_type: "content_plan" | "campaign_opportunities" | "business_growth";
  idempotency_key: string;
  window_start: string;
  window_end: string;
  status:
    | "queued"
    | "running"
    | "completed"
    | "provider_unavailable"
    | "blocked_entitlement"
    | "failed";
  proposal_count: number;
  failure_code: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type MarketingActionProposal = {
  id: string;
  business_id: string;
  entity_type: "campaign" | "content";
  entity_id: string;
  channel: string;
  connector_type: string;
  execution_id: string;
  ai_action_id: string;
  action_type: string;
  action_status: string;
  policy_decision: "allow" | "require_approval" | "block" | null;
  policy_reason_code: string | null;
  approval_id: string | null;
  approval_status:
    "pending" | "approved" | "rejected" | "expired" | "canceled" | null;
  connector_state:
    "connection_required" | "provider_disabled" | "ready_after_approval";
  connector_message: string;
  created_at: string;
  updated_at: string;
};

export type CompetitorObservation = {
  id: string;
  business_id: string;
  competitor_id: string;
  observed_at: string;
  category:
    | "pricing"
    | "product"
    | "marketing"
    | "content"
    | "positioning"
    | "promotion"
    | "social"
    | "website"
    | "offer";
  title: string;
  summary: string;
  source_type: "manual" | "import" | "ai_research";
  source_reference: string | null;
  safe_metrics: Record<string, string | number | null>;
  created_at: string;
  updated_at: string;
};

export type CompetitorAnalysis = {
  id: string;
  business_id: string;
  competitor_id: string;
  summary: string;
  strengths: string[];
  weaknesses: string[];
  differences: string[];
  positioning_gaps: string[];
  content_gaps: string[];
  campaign_opportunities: string[];
  recommendations: string[];
  source_observation_count: number;
  generated_by: "user" | "ai";
  created_at: string;
  updated_at: string;
};

export type MarketingTrend = {
  id: string;
  business_id: string;
  title: string;
  category: string;
  description: string;
  source: "manual" | "import" | "ai_research";
  source_reference: string | null;
  observed_at: string;
  relevance_score: string;
  confidence: string | null;
  status: MarketingTrendStatus;
  opportunity_id: string | null;
  created_at: string;
  updated_at: string;
};

export type MarketingPerformance = {
  id: string;
  business_id: string;
  campaign_id: string;
  content_id: string | null;
  channel: MarketingChannel;
  period_start: string;
  period_end: string;
  data_source: "manual" | "import" | "future_connector";
  spend: string;
  impressions: number;
  reach: number;
  clicks: number;
  leads: number;
  conversions: number;
  revenue: string;
  ctr: string;
  cpc: string;
  cpm: string;
  cpl: string;
  cpa: string;
  roas: string;
  attribution_class:
    | "provider_attributed"
    | "first_party_observed"
    | "ai_business_os_derived"
    | "unknown";
  external_campaign_reference: string | null;
  created_at: string;
  updated_at: string;
};

export type MarketingAnalyticsBreakdown = {
  label: string;
  spend: string;
  impressions: number;
  clicks: number;
  leads: number;
  conversions: number;
  revenue: string;
  ctr: string;
  cpc: string;
  roas: string;
};

export type MarketingAnalytics = {
  period_start: string;
  period_end: string;
  currency: string;
  spend: string;
  impressions: number;
  reach: number;
  clicks: number;
  leads: number;
  conversions: number;
  revenue: string;
  ctr: string;
  cpc: string;
  cpl: string;
  cpa: string;
  roas: string;
  campaigns: MarketingAnalyticsBreakdown[];
  channels: MarketingAnalyticsBreakdown[];
  top_content: Array<{
    content_id: string;
    title: string;
    channel: MarketingChannel;
    clicks: number;
    conversions: number;
    revenue: string;
  }>;
  trends: Array<{
    label: string;
    spend: string;
    revenue: string;
    impressions: number;
    clicks: number;
    conversions: number;
  }>;
};

export type AdvertisingSpendPolicy = {
  id: string;
  business_id: string;
  currency: string;
  max_single_campaign_budget: string;
  max_single_budget_change: string;
  daily_advertising_limit: string | null;
  monthly_ai_managed_limit: string | null;
  active: boolean;
  set_by_user_id: string | null;
  created_at: string;
  updated_at: string;
};

export type GrowthExperimentStatus =
  | "draft"
  | "ready"
  | "running"
  | "completed"
  | "evaluated"
  | "canceled";

export type GrowthMetric = "ctr" | "conversion_rate" | "cpc" | "cpa" | "roas";

export type GrowthAttribution =
  | "provider_attributed"
  | "first_party_observed";

export type GrowthVariantMetric = {
  variant_id: string;
  variant_key: string;
  is_control: boolean;
  performance_row_count: number;
  sample_basis: string;
  sample_size: number;
  minimum_sample_size: number;
  metric_value: string | null;
  spend: string;
  impressions: number;
  clicks: number;
  conversions: number;
  revenue: string;
  data_quality: "complete" | "too_many_rows" | "overlapping_periods";
  sufficient: boolean;
};

export type GrowthExperimentResult = {
  id: string;
  business_id: string;
  experiment_id: string;
  classification:
    | "insufficient_evidence"
    | "no_material_difference"
    | "observed_directional_difference"
    | "mixed_result";
  primary_metric: GrowthMetric;
  attribution_classification: GrowthAttribution;
  currency: string;
  control_value: string | null;
  directional_leader_value: string | null;
  absolute_difference: string | null;
  relative_difference: string | null;
  evidence_quality: string;
  directional_leader_variant_id: string | null;
  directional_leader_key: string | null;
  learning_memory_id: string | null;
  measurement_start: string;
  measurement_end: string;
  evaluation_cutoff: string;
  evaluated_at: string;
  evaluation_revision: string;
  evidence: {
    formula_version: string;
    primary_metric: GrowthMetric;
    metric_direction: "higher_is_better" | "lower_is_better";
    attribution_classification: GrowthAttribution;
    currency: string;
    measurement_start: string;
    measurement_end: string;
    evaluation_cutoff: string;
    fact_created_at_policy: "measurement_start_through_evaluation_cutoff";
    minimum_sample_size: number;
    sample_basis: string;
    material_relative_difference: string;
    statistical_significance_test: null;
    causal_claim_allowed: false;
    variant_metrics: GrowthVariantMetric[];
  };
  created_at: string;
  updated_at: string;
};

export type GrowthExperimentVariant = {
  id: string;
  business_id: string;
  experiment_id: string;
  variant_key: string;
  label: string;
  is_control: boolean;
  campaign_id: string;
  content_id: string | null;
  created_at: string;
  updated_at: string;
};

export type GrowthExperiment = {
  id: string;
  business_id: string;
  name: string;
  hypothesis: string;
  learning_key: string;
  experiment_type: "campaign" | "content";
  status: GrowthExperimentStatus;
  primary_metric: GrowthMetric;
  attribution_classification: GrowthAttribution;
  currency: string;
  evaluation_window_days: number;
  minimum_sample_size: number;
  definition_version: number;
  source_opportunity_id: string | null;
  source_ai_action_id: string | null;
  created_by_user_id: string | null;
  measurement_start: string | null;
  measurement_end: string | null;
  evaluation_cutoff: string | null;
  completed_at: string | null;
  canceled_at: string | null;
  variants: GrowthExperimentVariant[];
  result: GrowthExperimentResult | null;
  created_at: string;
  updated_at: string;
};

export type GrowthLearning = {
  id: string;
  content: string;
  confidence: string;
  importance: number;
  status: "active" | "superseded" | "archived";
  occurred_at: string | null;
  last_reinforced_at: string | null;
  created_at: string;
  updated_at: string;
};
