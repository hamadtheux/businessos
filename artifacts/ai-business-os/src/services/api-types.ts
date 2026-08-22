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
  status?: CatalogItemStatus;
};

export type CatalogItemUpdate = {
  item_type?: CatalogItemType;
  name?: string;
  description?: string | null;
  sku?: string | null;
  price?: string | null;
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
  "business_profile" | "branding" | "catalog_item" | "knowledge_entry";

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
  detail?: string | ApiValidationIssue[];
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
  membership_role: string;
  created_at: string;
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
  branding?: BusinessBrandingInput;
};

export type BusinessOnboardingResponse = {
  business: BusinessSummary;
  branding: BusinessBrandingResponse | null;
  created: boolean;
};
