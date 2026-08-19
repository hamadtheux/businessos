export type WorkspaceCustomer = {
  id: number;
  name: string;
  email: string;
  phone: string;
  company?: string;
  orders: number;
  spent: number;
  segment: string;
  lastActive: string;
  notes: string[];
};

export type WorkspaceOrder = {
  id: string;
  customer: string;
  items: string;
  total: number;
  subtotal: number;
  delivery: number;
  payment: string;
  status: string;
  date: string;
  source: string;
  notes: string;
};

export type WorkspaceLead = {
  id: number;
  name: string;
  company: string;
  email: string;
  phone: string;
  source: string;
  score: number;
  value: string;
  stage: string;
  contact: string;
  notes: string;
  intent: string;
  budgetSignal: string;
  urgency: string;
};

export type WorkspaceConversation = {
  id: number;
  name: string;
  channel: string;
  message: string;
  time: string;
  unread: boolean;
  ai: boolean;
  status: "Open" | "Escalated" | "Resolved";
  messages: Array<{
    id: string;
    author: "customer" | "ai" | "human";
    text: string;
    time: string;
  }>;
};

export type WorkspaceAgent = {
  id: string;
  name: string;
  role: string;
  active: boolean;
  tasks: number;
  success: number;
  lastActivity: string;
  autonomy: "Suggest" | "Approval" | "Autonomous";
  permissions: string[];
  tools: string[];
  goals: string[];
  escalation: string;
};

export type AgentActivity = {
  id: string;
  timestamp: string;
  agentId: string;
  agent: string;
  action: string;
  entity: string;
  type: string;
  status: "Completed" | "Failed" | "Running";
  result: string;
  approval: "Not required" | "Approved" | "Pending";
};

export type WorkspaceApproval = {
  id: number;
  agent: string;
  title: string;
  reason: string;
  status: "Pending" | "Approved" | "Rejected" | "Expired";
  impact: string;
};

export type WorkspaceOpportunity = {
  id: number;
  title: string;
  copy: string;
  category: string;
  impact: string;
  reviewed: boolean;
};

export type WorkspaceNotification = {
  id: string;
  type: string;
  title: string;
  detail: string;
  priority: "High" | "Medium" | "Low";
  timestamp: string;
  read: boolean;
  href: string;
};

export type AuditEvent = {
  id: string;
  timestamp: string;
  actor: string;
  actorType: "AI agent" | "Human user" | "System";
  action: string;
  entity: string;
  before?: string;
  after?: string;
  status: "Completed" | "Failed" | "Pending";
  approval?: string;
  source: string;
};

export type Competitor = {
  id: string;
  name: string;
  website: string;
  industry: string;
  status: "Ready" | "Analyzing" | "Needs analysis";
  positioning: string;
  products: string;
  pricing: string;
  marketing: string;
  offers: string;
  strengths: string[];
  weaknesses: string[];
  opportunities: string[];
  lastAnalyzed: string;
  summary: string;
};

export type Trend = {
  id: string;
  topic: string;
  source: string;
  strength: "Strong" | "Rising" | "Emerging";
  relevance: number;
  industryRelevance: string;
  velocity: string;
  why: string;
  recommendation: string;
  state: "New" | "Saved" | "Ignored";
};

export type Campaign = {
  id: string;
  name: string;
  status:
    | "Draft"
    | "Awaiting approval"
    | "Scheduled"
    | "Running"
    | "Completed"
    | "Paused";
  goal: string;
  audience: string;
  channels: string[];
  content: string;
  schedule: string;
  kpis: string[];
  performance: string;
  analysis: string;
  recommendation: string;
};

export type SocialPost = {
  id: string;
  content: string;
  platform: string;
  calendarDay?: string;
  contentType?: string;
  reviewStatus?: "Approved";
  schedule: string;
  status: "Scheduled" | "Published" | "Draft" | "Needs approval";
  reach: number;
  engagement: number;
  clicks: number;
  analysis: string;
};

export type WorkspaceCatalogItem = {
  id: string;
  name: string;
  sku: string;
  price: number;
  availability: string;
  category: string;
  description: string;
};

export type WorkspaceCatalog = {
  method: "manual" | "upload" | "store" | "paste" | "skip";
  sourceName: string;
  storeProvider: "Shopify" | "WooCommerce" | "Custom Store / API" | null;
  confirmedAt: string;
  items: WorkspaceCatalogItem[];
};

export type WorkspaceIntegration = {
  id: string;
  name: string;
  category: string;
  description: string;
  connected: boolean;
  account: string;
  connectedDate: string;
  permissions: string[];
  dataAvailable: string[];
  lastSync: string;
  syncStatus: "Healthy" | "Syncing" | "Disconnected";
};

export type BrainSource = {
  id: string;
  name: string;
  category:
    | "Website"
    | "Documents"
    | "Products"
    | "FAQs"
    | "Policies"
    | "Brand Guidelines";
  type: "PDF" | "DOCX" | "TXT" | "CSV" | "Website";
  status: "Uploading" | "Processing" | "Processed" | "Failed";
  added: string;
};

export type WorkflowNode = {
  id: string;
  type:
    | "Trigger"
    | "AI Decision"
    | "Condition"
    | "Database"
    | "API"
    | "Action"
    | "Notification"
    | "Approval"
    | "Delay"
    | "Branch";
  label: string;
  config: string;
  branch?: "YES" | "NO";
};

export type Workflow = {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  nodes: WorkflowNode[];
};

export type AnalyticsData = {
  revenue: number;
  orders: number;
  customers: number;
  leads: number;
  conversion: number;
  averageOrder: number;
  repeatCustomers: number;
  revenueSeries: Array<{ label: string; revenue: number; orders: number }>;
  acquisition: Array<{ name: string; value: number }>;
};

export type WorkspaceData = {
  catalog: WorkspaceCatalog;
  customers: WorkspaceCustomer[];
  orders: WorkspaceOrder[];
  leads: WorkspaceLead[];
  conversations: WorkspaceConversation[];
  agents: WorkspaceAgent[];
  agentActivity: AgentActivity[];
  approvals: WorkspaceApproval[];
  opportunities: WorkspaceOpportunity[];
  notifications: WorkspaceNotification[];
  audit: AuditEvent[];
  competitors: Competitor[];
  trends: Trend[];
  campaigns: Campaign[];
  socialPosts: SocialPost[];
  integrations: WorkspaceIntegration[];
  brainSources: BrainSource[];
  workflows: Workflow[];
  analytics: AnalyticsData;
};
