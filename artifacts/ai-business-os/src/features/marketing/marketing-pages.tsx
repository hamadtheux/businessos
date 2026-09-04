import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  BarChart3,
  Calendar,
  Check,
  Copy,
  Eye,
  Facebook,
  Instagram,
  Linkedin,
  Pause,
  Play,
  Plus,
  Send,
  Sparkles,
  Target,
  Wand2,
  X,
} from "lucide-react";
import { Link, useLocation } from "wouter";
import { useBusiness } from "@/business-context";
import {
  Badge,
  Button,
  Card,
  Modal,
  PageHeader,
  SectionTitle,
} from "@/components/product-ui";
import {
  CmoCreativePanel,
} from "@/features/marketing/cmo-creative-panel";
import {
  creativeFormatForContent,
  creativePhaseForDisplay,
  creativeResultNotice,
  runCreativeOperationWithRecovery,
  type CreativeProgress,
} from "@/lib/cmo-ux";
import { humanizeApiError } from "@/services/api-client";
import { catalogApi } from "@/services/catalog";
import { integrationsApi } from "@/services/integrations";
import type {
  CampaignStatus,
  CreativeAsset,
  MarketingCampaign,
  MarketingChannel,
  MarketingContent,
  MarketingContentStatus,
  SocialSchedule,
} from "@/services/api-types";
import { marketingApi, type CampaignPreflight } from "@/services/marketing";

export const cmoTabs = [
  ["Overview", "/marketing"],
  ["Content", "/marketing/content"],
  ["Calendar", "/marketing/calendar"],
  ["Campaigns", "/marketing/campaigns"],
  ["Social", "/marketing/social"],
  ["Competitors", "/competitors"],
  ["Trends", "/trends"],
  ["Performance", "/marketing/performance"],
] as const;

const campaignTransitions: Record<CampaignStatus, CampaignStatus[]> = {
  draft: ["planned", "awaiting_approval", "canceled"],
  planned: ["draft", "awaiting_approval", "canceled"],
  awaiting_approval: ["draft", "approved", "canceled"],
  approved: ["scheduled", "executing", "canceled"],
  executing: [
    "provider_pending",
    "paused",
    "failed",
    "attention_required",
    "unknown_external_state",
  ],
  provider_pending: [
    "active",
    "paused",
    "failed",
    "attention_required",
    "unknown_external_state",
  ],
  scheduled: ["active", "canceled"],
  active: ["paused", "completed", "canceled"],
  paused: ["active", "completed", "canceled"],
  completed: [],
  canceled: [],
  failed: ["draft", "attention_required"],
  attention_required: [
    "draft",
    "awaiting_approval",
    "executing",
    "paused",
    "canceled",
  ],
  unknown_external_state: ["paused", "attention_required"],
};
const channels: MarketingChannel[] = [
  "instagram",
  "facebook",
  "linkedin",
  "tiktok",
  "email",
  "whatsapp",
  "website",
  "meta",
  "google_ads",
];
const platformIcons = {
  instagram: Instagram,
  facebook: Facebook,
  linkedin: Linkedin,
};

export function CmoDepartmentNav({ active }: { active: string }) {
  return (
    <div className="department-tabs">
      {cmoTabs.map(([label, href]) => (
        <Link
          className={`department-tab ${active === label ? "active" : ""}`}
          href={href}
          key={label}
        >
          {label}
        </Link>
      ))}
    </div>
  );
}

function ErrorCard({
  error,
  retry,
  title = "Marketing data unavailable",
}: {
  error: unknown;
  retry: () => void;
  title?: string;
}) {
  return (
    <Card>
      <div className="empty">
        <AlertCircle />
        <h3>{title}</h3>
        <p>{humanizeApiError(error, "Try again in a moment.")}</p>
        <Button onClick={retry}>Try again</Button>
      </div>
    </Card>
  );
}

function EmptyCard({
  icon,
  title,
  copy,
}: {
  icon: React.ReactNode;
  title: string;
  copy: string;
}) {
  return (
    <Card>
      <div className="empty">
        {icon}
        <h3>{title}</h3>
        <p>{copy}</p>
      </div>
    </Card>
  );
}

function Pager({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (value: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return (
    <div className="table-toolbar">
      <span className="subtle">
        Page {page} of {pages} · {total} records
      </span>
      <div className="toolbar">
        <Button
          className="btn-sm"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          Previous
        </Button>
        <Button
          className="btn-sm"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}

function selectedChannels(form: FormData): MarketingChannel[] {
  return form.getAll("channels").map(String) as MarketingChannel[];
}

function campaignTone(status: CampaignStatus) {
  if (status === "active" || status === "completed") return "success";
  if (
    [
      "awaiting_approval",
      "approved",
      "scheduled",
      "executing",
      "provider_pending",
    ].includes(status)
  )
    return "warning";
  if (
    [
      "paused",
      "canceled",
      "failed",
      "attention_required",
      "unknown_external_state",
    ].includes(status)
  )
    return "danger";
  return "neutral";
}

function proposalValue(value: unknown, fallback?: string) {
  const empty = fallback ?? "Not supplied";
  if (Array.isArray(value))
    return value.length ? value.map(String).join(" · ") : empty;
  if (typeof value === "string" || typeof value === "number")
    return String(value);
  return empty;
}

function CampaignProposalReview({
  campaign,
  preflight,
}: {
  campaign: MarketingCampaign;
  preflight?: CampaignPreflight;
}) {
  const proposal = campaign.normalized_proposal ?? {};
  return (
    <>
      <SectionTitle
        title="Normalized campaign review"
        action={
          <Badge tone={preflight?.ready ? "success" : "warning"}>
            {preflight?.ready
              ? "Preflight ready"
              : campaign.campaign_type
                ? "Preflight blocked"
                : "Internal proposal"}
          </Badge>
        }
      />
      <div className="analysis-grid">
        <Card>
          <div className="eyebrow">Products & feed</div>
          <p className="detail-copy">
            {campaign.catalog_item_ids.length} selected ·{" "}
            {preflight
              ? `${preflight.eligible_products}/${preflight.selected_products} provider eligible`
              : proposalValue(proposal.feed_eligibility)}
          </p>
        </Card>
        <Card>
          <div className="eyebrow">Provider & campaign type</div>
          <p className="detail-copy">
            {proposalValue(campaign.recommended_provider)} ·{" "}
            {proposalValue(campaign.campaign_type).replaceAll("_", " ")}
          </p>
        </Card>
        <Card>
          <div className="eyebrow">Audience strategy</div>
          <p className="detail-copy">
            {proposalValue(
              proposal.audience_strategy,
              campaign.audience_definition,
            )}
          </p>
        </Card>
        <Card>
          <div className="eyebrow">Creative & CTA</div>
          <p className="detail-copy">
            {proposalValue(
              proposal.creative_angle,
              campaign.creative_brief ?? undefined,
            )}{" "}
            · {proposalValue(proposal.cta, campaign.proposed_cta ?? undefined)}
          </p>
        </Card>
        <Card>
          <div className="eyebrow">Offer governance</div>
          <p className="detail-copy">
            {campaign.offer || "No offer proposed"} · source{" "}
            {campaign.offer_source.replaceAll("_", " ")} ·{" "}
            {campaign.offer_authorized ? "authorized" : "not authorized"}
          </p>
        </Card>
        <Card>
          <div className="eyebrow">Measurement</div>
          <p className="detail-copy">
            {proposalValue(
              proposal.measurement_plan,
              campaign.measurement_plan ?? undefined,
            )}
          </p>
        </Card>
      </div>
      {preflight && !preflight.ready && (
        <div className="recommendation-strip">
          <AlertCircle />
          <div>
            <strong>Resolve before external execution</strong>
            {preflight.issues.map((issue) => (
              <p key={`${issue.code}:${issue.message}`}>{issue.message}</p>
            ))}
          </div>
        </div>
      )}
      <div className="chip-list">
        {campaign.source_evidence?.map((item, index) => (
          <Badge
            tone="info"
            key={`${String(item.classification ?? item.source_type ?? "evidence")}:${index}`}
          >
            {String(
              item.classification ?? item.source_type ?? "evidence",
            ).replaceAll("_", " ")}
          </Badge>
        ))}
      </div>
    </>
  );
}

export function CampaignsPage() {
  const { activeBusinessId, activeBusiness } = useBusiness();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const [creating, setCreating] = useState(() =>
    new URLSearchParams(window.location.search).has("new"),
  );
  const [manualCampaign, setManualCampaign] = useState(false);
  const [editingCampaign, setEditingCampaign] = useState(false);
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(1);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const promotedProductId =
    new URLSearchParams(window.location.search).get("product") || "";
  const promotedProduct = useQuery({
    queryKey: ["catalog", activeBusinessId, "promote", promotedProductId],
    queryFn: () =>
      catalogApi.getCatalogItem(activeBusinessId, promotedProductId),
    enabled: Boolean(activeBusinessId && promotedProductId),
  });

  const list = useQuery({
    queryKey: ["marketing", activeBusinessId, "campaigns", filter, page],
    queryFn: ({ signal }) =>
      marketingApi.campaigns.list(
        activeBusinessId,
        { page, pageSize: 24, status: filter || undefined },
        signal,
      ),
    enabled: Boolean(activeBusinessId),
  });
  const detail = useQuery({
    queryKey: ["marketing", activeBusinessId, "campaign", selectedId],
    queryFn: ({ signal }) =>
      marketingApi.campaigns.get(activeBusinessId, selectedId, signal),
    enabled: Boolean(activeBusinessId && selectedId),
  });
  const content = useQuery({
    queryKey: [
      "marketing",
      activeBusinessId,
      "content",
      "campaign",
      selectedId,
    ],
    queryFn: ({ signal }) =>
      marketingApi.content.list(
        activeBusinessId,
        { pageSize: 100 },
        { campaignId: selectedId },
        signal,
      ),
    enabled: Boolean(activeBusinessId && selectedId),
  });
  const performance = useQuery({
    queryKey: [
      "marketing",
      activeBusinessId,
      "performance",
      "campaign",
      selectedId,
    ],
    queryFn: ({ signal }) =>
      marketingApi.performance.list(
        activeBusinessId,
        { pageSize: 100 },
        { campaignId: selectedId },
        signal,
      ),
    enabled: Boolean(activeBusinessId && selectedId),
  });
  const commerceActionChannel =
    detail.data?.campaign_type === "retail_performance_max"
      ? "google_ads"
      : detail.data?.campaign_type === "catalog_sales"
        ? "meta"
        : undefined;
  const preflight = useQuery({
    queryKey: [
      "marketing",
      activeBusinessId,
      "campaign-preflight",
      selectedId,
      commerceActionChannel,
    ],
    queryFn: ({ signal }) =>
      marketingApi.campaigns.preflight(
        activeBusinessId,
        selectedId,
        commerceActionChannel,
        signal,
      ),
    enabled: Boolean(activeBusinessId && selectedId && commerceActionChannel),
    retry: false,
  });
  const opportunityRun = useQuery({
    queryKey: [
      "marketing",
      activeBusinessId,
      "automation",
      "campaign_opportunities",
    ],
    queryFn: ({ signal }) =>
      marketingApi.automation.get(
        activeBusinessId,
        "campaign_opportunities",
        signal,
      ),
    enabled: Boolean(activeBusinessId),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: ["marketing", activeBusinessId],
    });
  const create = useMutation({
    mutationFn: (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const selected = selectedChannels(form);
      const input = {
        name: String(form.get("name")) || null,
        goal: String(form.get("objective")),
        audience_definition: String(form.get("audience")) || null,
        channels: selected,
        planned_budget: String(form.get("budget") || "0"),
        budget_mode: String(form.get("budget_mode")) as "daily" | "lifetime",
        start_date: String(form.get("start_date")) || null,
        end_date: String(form.get("end_date")) || null,
        catalog_item_ids: promotedProductId ? [promotedProductId] : [],
        offer: String(form.get("offer")) || null,
        offer_authorized: form.get("offer_authorized") === "on",
      };
      if (!manualCampaign)
        return marketingApi.campaigns.generate(activeBusinessId, input);
      if (!input.name || !input.audience_definition || !input.channels.length)
        throw new Error(
          "Manual mode requires a name, audience, and at least one channel.",
        );
      return marketingApi.campaigns.create(activeBusinessId, {
        name: input.name,
        objective: input.goal,
        audience_definition: input.audience_definition,
        channels: input.channels,
        planned_budget: input.planned_budget,
        budget_mode: input.budget_mode,
        start_date: input.start_date,
        end_date: input.end_date,
        description: String(form.get("description")) || null,
      });
    },
    onSuccess: (campaign) => {
      setCreating(false);
      setSelectedId(campaign.id);
      setNotice(`Internal campaign “${campaign.name}” was created for review.`);
      setError("");
      void invalidate();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(reason, "Campaign draft could not be created."),
      ),
  });
  const move = useMutation({
    mutationFn: ({ id, status }: { id: string; status: CampaignStatus }) =>
      marketingApi.campaigns.status(activeBusinessId, id, status),
    onSuccess: (campaign) => {
      setNotice(
        `Campaign is now ${campaign.status.replaceAll("_", " ")}. No external action occurred.`,
      );
      setError("");
      void invalidate();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(reason, "Campaign status could not be changed."),
      ),
  });
  const duplicate = useMutation({
    mutationFn: (id: string) =>
      marketingApi.campaigns.duplicate(activeBusinessId, id),
    onSuccess: (campaign) => {
      setSelectedId(campaign.id);
      setNotice(`Campaign was duplicated as “${campaign.name}”.`);
      setError("");
      void invalidate();
    },
    onError: (reason) =>
      setError(humanizeApiError(reason, "Campaign could not be duplicated.")),
  });
  const update = useMutation({
    mutationFn: (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      return marketingApi.campaigns.update(activeBusinessId, selectedId, {
        name: String(form.get("name")),
        objective: String(form.get("objective")),
        audience_definition: String(form.get("audience")),
        planned_budget: String(form.get("budget") || "0"),
        budget_mode: String(form.get("budget_mode")) as "daily" | "lifetime",
        start_date: String(form.get("start_date")) || null,
        end_date: String(form.get("end_date")) || null,
        description: String(form.get("description")) || null,
        offer: String(form.get("offer")) || null,
      });
    },
    onSuccess: () => {
      setEditingCampaign(false);
      setNotice("Campaign changes were saved.");
      setError("");
      void invalidate();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(
          reason,
          "Campaign changes could not be saved. Check its dates, budget, and channel allocations.",
        ),
      ),
  });
  const addChannel = useMutation({
    mutationFn: (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      return marketingApi.campaigns.addChannel(activeBusinessId, selectedId, {
        channel: String(form.get("channel")) as MarketingChannel,
        objective: String(form.get("objective")),
        budget_allocation: String(form.get("budget_allocation") || "0"),
        audience_strategy: String(form.get("audience_strategy")),
        messaging: String(form.get("messaging")),
        planned_start: null,
        planned_end: null,
        safe_configuration: {
          placements: [],
          keywords: String(form.get("keywords") || "")
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean),
          call_to_action: String(form.get("cta")) || null,
          destination_path: null,
          optimization_goal: "conversions",
          notes: null,
        },
      });
    },
    onSuccess: (plan) => {
      setNotice(
        `${plan.channel} channel plan was added without external execution.`,
      );
      setError("");
      void invalidate();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(
          reason,
          "Channel plan could not be added. Check the campaign budget.",
        ),
      ),
  });
  const refreshOpportunities = useMutation({
    mutationFn: () =>
      marketingApi.automation.refresh(
        activeBusinessId,
        "campaign_opportunities",
      ),
    onSuccess: (run) => {
      setNotice(
        `Growth opportunity analysis ${run.status.replaceAll("_", " ")}.`,
      );
      setError("");
      void queryClient.invalidateQueries({
        queryKey: ["marketing", activeBusinessId],
      });
    },
    onError: (reason) =>
      setError(
        humanizeApiError(
          reason,
          "Growth opportunity analysis could not be scheduled.",
        ),
      ),
  });
  const prepareAction = useMutation({
    mutationFn: ({
      campaign,
      channel,
    }: {
      campaign: MarketingCampaign;
      channel?: "meta" | "google_ads";
    }) =>
      marketingApi.campaigns.prepareAction(
        activeBusinessId,
        campaign.id,
        channel,
      ),
    onSuccess: (proposal) => {
      setNotice(
        `${proposal.action_status.replaceAll("_", " ")}: ${proposal.connector_message}`,
      );
      setError("");
      void invalidate();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(
          reason,
          "A governed ad action could not be prepared. The campaign needs a supported ad channel and trusted ISO geography.",
        ),
      ),
  });

  const selected = detail.data;
  const money = (value: string) =>
    new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: activeBusiness?.currency || "USD",
    }).format(Number(value));
  const totals = useMemo(
    () =>
      performance.data?.items.reduce(
        (sum, item) => ({
          spend: sum.spend + Number(item.spend),
          revenue: sum.revenue + Number(item.revenue),
          conversions: sum.conversions + item.conversions,
        }),
        { spend: 0, revenue: 0, conversions: 0 },
      ),
    [performance.data],
  );

  return (
    <>
      <PageHeader
        eyebrow="AI CMO · Recommended growth"
        title="Campaigns"
        subtitle="Describe the outcome. AI builds an evidence-backed audience, channel, message, creative, budget, and measurement proposal for review."
        action={
          <div className="toolbar">
            <Button
              onClick={() => {
                setManualCampaign(true);
                setCreating(true);
              }}
            >
              <Plus /> Advanced · Build manually
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                setManualCampaign(false);
                setCreating(true);
              }}
              data-testid="button-create-campaign"
            >
              <Sparkles /> Generate campaign
            </Button>
          </div>
        }
      />
      <CmoDepartmentNav active="Campaigns" />
      {notice && (
        <div className="ai-banner">
          <Check /> {notice}
          <button className="close-btn" onClick={() => setNotice("")}>
            <X />
          </button>
        </div>
      )}
      {error && (
        <div className="ai-banner">
          <AlertCircle /> {error}
          <button className="close-btn" onClick={() => setError("")}>
            <X />
          </button>
        </div>
      )}
      <Card className="intelligence-hero">
        <div className="intelligence-hero-icon">
          <Target />
        </div>
        <div className="row-main">
          <div className="eyebrow">Growth opportunities</div>
          <h2>
            {opportunityRun.data
              ? `${opportunityRun.data.proposal_count} evidence-backed proposal${opportunityRun.data.proposal_count === 1 ? "" : "s"} in the latest window`
              : "AI checks sourced competitor and trend signals automatically"}
          </h2>
          <p className="subtle">
            {opportunityRun.data
              ? `Latest bounded analysis is ${opportunityRun.data.status.replaceAll("_", " ")}. Opportunities remain proposals until reviewed.`
              : "No opportunity run has completed yet. Empty evidence produces no fabricated opportunity."}
          </p>
        </div>
        <Button
          variant="green"
          disabled={refreshOpportunities.isPending}
          onClick={() => refreshOpportunities.mutate()}
        >
          <Sparkles />{" "}
          {refreshOpportunities.isPending
            ? "Scheduling…"
            : "Analyze opportunities"}
        </Button>
      </Card>
      <div className="tabs">
        {[
          "",
          "draft",
          "planned",
          "awaiting_approval",
          "approved",
          "executing",
          "provider_pending",
          "scheduled",
          "active",
          "paused",
          "attention_required",
          "failed",
          "unknown_external_state",
          "completed",
          "canceled",
        ].map((item) => (
          <button
            className={`tab ${filter === item ? "active" : ""}`}
            onClick={() => {
              setFilter(item);
              setPage(1);
            }}
            key={item || "all"}
          >
            {item ? item.replaceAll("_", " ") : "All"}
          </button>
        ))}
      </div>
      {list.isError ? (
        <ErrorCard error={list.error} retry={() => void list.refetch()} />
      ) : (
        <>
          <div className="grid campaign-grid">
            {list.data?.items.map((campaign) => (
              <Card className="campaign-card" key={campaign.id}>
                <div className="campaign-card-head">
                  <div className="campaign-mark">
                    <Target />
                  </div>
                  <Badge tone={campaignTone(campaign.status)}>
                    {campaign.status.replaceAll("_", " ")}
                  </Badge>
                </div>
                <h2>{campaign.name}</h2>
                <p className="subtle">{campaign.objective}</p>
                <div className="campaign-meta">
                  <div>
                    <span>Audience</span>
                    <strong>{campaign.audience_definition}</strong>
                  </div>
                  <div>
                    <span>Budget</span>
                    <strong>
                      {money(campaign.planned_budget)} · {campaign.budget_mode}
                    </strong>
                  </div>
                </div>
                <div className="chip-list">
                  {campaign.channels.map((channel) => (
                    <Badge tone="info" key={channel}>
                      {channel}
                    </Badge>
                  ))}
                </div>
                <div className="toolbar">
                  <Button
                    variant="soft"
                    className="btn-sm"
                    onClick={() => setSelectedId(campaign.id)}
                  >
                    <Eye /> View campaign
                  </Button>
                  <Button
                    className="btn-sm"
                    disabled={duplicate.isPending}
                    onClick={() => duplicate.mutate(campaign.id)}
                  >
                    <Copy /> Duplicate
                  </Button>
                </div>
              </Card>
            ))}
            {list.isLoading && (
              <Card>
                <div className="empty">
                  <p>Loading campaigns…</p>
                </div>
              </Card>
            )}
            {list.data && !list.data.items.length && (
              <EmptyCard
                icon={<Target />}
                title="No campaigns in this view"
                copy="Create an internal campaign draft. Nothing will be launched externally."
              />
            )}
          </div>
          {list.data && (
            <Pager
              page={page}
              pageSize={list.data.page_size}
              total={list.data.total}
              onPage={setPage}
            />
          )}
        </>
      )}
      {creating && (
        <Modal
          title={
            manualCampaign
              ? "Advanced · Build campaign"
              : "What do you want to achieve?"
          }
          description={
            manualCampaign
              ? "Manual correction/fallback. Nothing is executed externally."
              : "AI CMO uses trusted Business Brain, catalog, customer, performance, competitor, trend, and memory context where legitimately available."
          }
          onClose={() => setCreating(false)}
        >
          <form onSubmit={(event) => create.mutate(event)}>
            {promotedProductId && (
              <div className="recommendation-strip">
                <Target />
                <div>
                  <div className="eyebrow">Promote with AI</div>
                  <p>
                    {promotedProduct.isLoading
                      ? "Loading authoritative product context…"
                      : promotedProduct.data
                        ? `${promotedProduct.data.name} · ${promotedProduct.data.price ?? "price unavailable"} ${promotedProduct.data.currency ?? activeBusiness?.currency ?? ""} · ${promotedProduct.data.availability.replaceAll("_", " ")} · source ${promotedProduct.data.source}`
                        : "The selected product could not be loaded; campaign generation will fail closed."}
                  </p>
                </div>
              </div>
            )}
            <div className="form-grid">
              <div className="field full">
                <label>Business goal</label>
                <textarea
                  name="objective"
                  required
                  maxLength={1000}
                  defaultValue={
                    promotedProduct.data
                      ? `Increase sales of ${promotedProduct.data.name}`
                      : ""
                  }
                  placeholder="Get more qualified leads, promote a real product, or increase repeat purchase…"
                />
              </div>
              <div className="field">
                <label>Budget guidance</label>
                <input
                  name="budget"
                  type="number"
                  min="0"
                  max="1000000000"
                  step="0.01"
                  defaultValue="0"
                />
              </div>
              <div className="field">
                <label>Budget period</label>
                <select name="budget_mode">
                  <option value="lifetime">Lifetime guidance</option>
                  <option value="daily">Daily guidance</option>
                </select>
              </div>
              {manualCampaign ? (
                <>
                  <div className="field full">
                    <label>Campaign name</label>
                    <input name="name" required maxLength={180} />
                  </div>
                  <div className="field full">
                    <label>Audience override</label>
                    <textarea name="audience" required maxLength={3000} />
                  </div>
                  <div className="field full">
                    <label>Description</label>
                    <textarea name="description" maxLength={5000} />
                  </div>
                  <div className="field full">
                    <label>Channels</label>
                    <div className="checkbox-row">
                      {channels.map((channel) => (
                        <label key={channel}>
                          <input
                            type="checkbox"
                            name="channels"
                            value={channel}
                          />{" "}
                          {channel.replaceAll("_", " ")}
                        </label>
                      ))}
                    </div>
                  </div>
                </>
              ) : (
                <details className="field full">
                  <summary>Optional guidance</summary>
                  <div className="form-grid section-gap">
                    <label className="field full">
                      <span>Name override</span>
                      <input name="name" maxLength={180} />
                    </label>
                    <label className="field full">
                      <span>Audience note</span>
                      <textarea
                        name="audience"
                        maxLength={3000}
                        placeholder="Optional only—AI Audience Intelligence will build the evidence-backed hypothesis."
                      />
                    </label>
                    <label className="field full">
                      <span>Owner-authorized offer (optional)</span>
                      <textarea
                        name="offer"
                        maxLength={2000}
                        placeholder="Only enter a promotion the business has explicitly authorized."
                      />
                    </label>
                    <label className="field full">
                      <span>
                        <input type="checkbox" name="offer_authorized" /> I
                        confirm this offer is authorized by the business owner
                      </span>
                    </label>
                    <div className="field full">
                      <label>Preferred channels (optional)</label>
                      <div className="checkbox-row">
                        {channels.map((channel) => (
                          <label key={channel}>
                            <input
                              type="checkbox"
                              name="channels"
                              value={channel}
                            />{" "}
                            {channel.replaceAll("_", " ")}
                          </label>
                        ))}
                      </div>
                    </div>
                  </div>
                </details>
              )}
              <div className="field">
                <label>Start (optional)</label>
                <input name="start_date" type="date" />
              </div>
              <div className="field">
                <label>End (optional)</label>
                <input name="end_date" type="date" />
              </div>
            </div>
            {error && <p className="form-error">{error}</p>}
            <div className="modal-foot">
              <Button type="button" onClick={() => setCreating(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                type="submit"
                disabled={
                  create.isPending ||
                  (Boolean(promotedProductId) && !promotedProduct.data)
                }
              >
                <Sparkles />{" "}
                {create.isPending
                  ? "Preparing strategy…"
                  : manualCampaign
                    ? "Save manual draft"
                    : "Generate evidence-backed proposal"}
              </Button>
            </div>
          </form>
        </Modal>
      )}
      {selectedId && (
        <Modal
          wide
          title={selected?.name || "Campaign"}
          description="Internal planning record · external connection required for publishing or spend"
          onClose={() => {
            setSelectedId("");
            setEditingCampaign(false);
          }}
        >
          {detail.isLoading ? (
            <div className="empty">
              <p>Loading campaign…</p>
            </div>
          ) : detail.isError ? (
            <ErrorCard
              error={detail.error}
              retry={() => void detail.refetch()}
            />
          ) : (
            selected && (
              <>
                <div className="campaign-detail-hero">
                  <div>
                    <div className="eyebrow">Objective</div>
                    <h2>{selected.objective}</h2>
                    <p>{selected.audience_definition}</p>
                  </div>
                  <Badge tone={campaignTone(selected.status)}>
                    {selected.status.replaceAll("_", " ")}
                  </Badge>
                </div>
                <div className="toolbar" style={{ marginBottom: 18 }}>
                  <Button
                    className="btn-sm"
                    onClick={() => setEditingCampaign(true)}
                  >
                    <Copy /> Advanced · Edit
                  </Button>
                  {selected.channels.includes("meta") && (
                    <Button
                      variant="primary"
                      disabled={
                        prepareAction.isPending ||
                        (selected.campaign_type === "catalog_sales" &&
                          !preflight.data?.ready)
                      }
                      onClick={() =>
                        prepareAction.mutate({
                          campaign: selected,
                          channel: "meta",
                        })
                      }
                    >
                      <Send />{" "}
                      {selected.campaign_type === "catalog_sales"
                        ? "Send Meta launch to approval"
                        : "Prepare Meta action"}
                    </Button>
                  )}
                  {selected.channels.includes("google_ads") && (
                    <Button
                      variant="primary"
                      disabled={
                        prepareAction.isPending ||
                        (selected.campaign_type === "retail_performance_max" &&
                          !preflight.data?.ready)
                      }
                      onClick={() =>
                        prepareAction.mutate({
                          campaign: selected,
                          channel: "google_ads",
                        })
                      }
                    >
                      <Send />{" "}
                      {selected.campaign_type === "retail_performance_max"
                        ? "Send Google launch to approval"
                        : "Prepare Google Ads action"}
                    </Button>
                  )}
                </div>
                {selected.ai_generated && (
                  <>
                    <SectionTitle
                      title="AI campaign proposal"
                      action={<Badge tone="info">Evidence-backed draft</Badge>}
                    />
                    <div className="analysis-grid">
                      <Card>
                        <div className="eyebrow">Reasoning</div>
                        <p className="detail-copy">
                          {selected.proposal_reasoning || selected.description}
                        </p>
                      </Card>
                      <Card>
                        <div className="eyebrow">Creative brief</div>
                        <p className="detail-copy">
                          {selected.creative_brief || "Not supplied"}
                        </p>
                      </Card>
                      <Card>
                        <div className="eyebrow">Proposed copy</div>
                        <p className="detail-copy">
                          {selected.proposed_copy || "Not supplied"}
                        </p>
                      </Card>
                      <Card>
                        <div className="eyebrow">Measurement</div>
                        <p className="detail-copy">
                          {selected.measurement_plan ||
                            "Measure only recorded outcomes."}
                        </p>
                      </Card>
                    </div>
                    <div className="chip-list">
                      {selected.required_integrations?.map((item) => (
                        <Badge tone="warning" key={item}>
                          Requires {item}
                        </Badge>
                      ))}
                    </div>
                  </>
                )}
                <CampaignProposalReview
                  campaign={selected}
                  preflight={preflight.data}
                />
                <div className="grid analytics-kpi-grid">
                  <Card className="kpi">
                    <div className="eyebrow">Planned budget</div>
                    <div className="kpi-value">
                      {money(selected.planned_budget)}
                    </div>
                  </Card>
                  <Card className="kpi">
                    <div className="eyebrow">Recorded spend</div>
                    <div className="kpi-value">
                      {money(String(totals?.spend ?? 0))}
                    </div>
                  </Card>
                  <Card className="kpi">
                    <div className="eyebrow">Recorded revenue</div>
                    <div className="kpi-value">
                      {money(String(totals?.revenue ?? 0))}
                    </div>
                  </Card>
                  <Card className="kpi">
                    <div className="eyebrow">Conversions</div>
                    <div className="kpi-value">{totals?.conversions ?? 0}</div>
                  </Card>
                </div>
                <div className="analysis-grid">
                  <Card>
                    <div className="eyebrow">Offer</div>
                    <p className="detail-copy">
                      {selected.offer || "No offer recorded"}
                    </p>
                  </Card>
                  <Card>
                    <div className="eyebrow">Schedule</div>
                    <p className="detail-copy">
                      {selected.start_date || "Not set"} –{" "}
                      {selected.end_date || "Not set"}
                    </p>
                  </Card>
                  <Card>
                    <div className="eyebrow">Content</div>
                    <p className="detail-copy">
                      {content.data?.total ?? 0} durable item(s)
                    </p>
                  </Card>
                  <Card>
                    <div className="eyebrow">Channel plans</div>
                    <p className="detail-copy">
                      {selected.channel_plans?.length ?? 0} configured
                      allocation(s)
                    </p>
                  </Card>
                </div>
                <SectionTitle title="Channel plans" />
                {selected.channel_plans?.map((plan) => (
                  <div className="list-row" key={plan.id}>
                    <div className="row-main">
                      <strong>{plan.channel}</strong>
                      <div className="row-copy">{plan.messaging}</div>
                    </div>
                    <Badge tone="info">{money(plan.budget_allocation)}</Badge>
                  </div>
                ))}
                {!selected.channel_plans?.length && (
                  <p className="subtle">No channel-specific allocation yet.</p>
                )}
                <details className="section-gap">
                  <summary>
                    <strong>Advanced · Add channel plan manually</strong>
                  </summary>
                  <form onSubmit={(event) => addChannel.mutate(event)}>
                    <div className="form-grid" style={{ marginTop: 18 }}>
                      <div className="field">
                        <label>Channel</label>
                        <select name="channel">
                          {selected.channels.map((channel) => (
                            <option key={channel} value={channel}>
                              {channel}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className="field">
                        <label>Allocation</label>
                        <input
                          name="budget_allocation"
                          type="number"
                          min="0"
                          step="0.01"
                          defaultValue="0"
                        />
                      </div>
                      <div className="field full">
                        <label>Objective</label>
                        <input
                          name="objective"
                          required
                          defaultValue={selected.objective}
                        />
                      </div>
                      <div className="field full">
                        <label>Audience strategy</label>
                        <textarea
                          name="audience_strategy"
                          required
                          defaultValue={selected.audience_definition}
                        />
                      </div>
                      <div className="field full">
                        <label>Messaging</label>
                        <textarea name="messaging" required />
                      </div>
                      <div className="field">
                        <label>Keywords</label>
                        <input
                          name="keywords"
                          placeholder="summer, local, quality"
                        />
                      </div>
                      <div className="field">
                        <label>CTA</label>
                        <input name="cta" />
                      </div>
                    </div>
                    <Button type="submit" disabled={addChannel.isPending}>
                      <Plus /> Add channel plan
                    </Button>
                  </form>
                </details>
                <div className="toolbar" style={{ marginTop: 18 }}>
                  {campaignTransitions[selected.status].map((next) => (
                    <Button
                      key={next}
                      variant={next === "canceled" ? "danger" : "green"}
                      disabled={move.isPending}
                      onClick={() =>
                        move.mutate({ id: selected.id, status: next })
                      }
                    >
                      {next === "awaiting_approval" ? (
                        <Send />
                      ) : next === "active" ? (
                        <Play />
                      ) : next === "paused" ? (
                        <Pause />
                      ) : (
                        <Check />
                      )}{" "}
                      {next === "active"
                        ? "Mark active internally"
                        : next.replaceAll("_", " ")}
                    </Button>
                  ))}
                </div>
                <div className="ai-banner">
                  <AlertCircle /> Prepared actions must pass policy and
                  approval. A configured provider creates paused or
                  provider-pending objects first; approval of the proposal does
                  not itself claim that the campaign is active.
                </div>
              </>
            )
          )}
        </Modal>
      )}
      {editingCampaign && selected && (
        <Modal
          title="Edit campaign"
          description="Update the internal campaign record. Existing channel allocations cannot exceed the revised budget."
          onClose={() => setEditingCampaign(false)}
        >
          <form onSubmit={(event) => update.mutate(event)}>
            <div className="form-grid">
              <div className="field full">
                <label>Campaign name</label>
                <input
                  name="name"
                  required
                  maxLength={180}
                  defaultValue={selected.name}
                />
              </div>
              <div className="field full">
                <label>Objective</label>
                <textarea
                  name="objective"
                  required
                  maxLength={1000}
                  defaultValue={selected.objective}
                />
              </div>
              <div className="field full">
                <label>Audience</label>
                <textarea
                  name="audience"
                  required
                  maxLength={3000}
                  defaultValue={selected.audience_definition}
                />
              </div>
              <div className="field">
                <label>Planned budget</label>
                <input
                  name="budget"
                  type="number"
                  min="0"
                  max="1000000000"
                  step="0.01"
                  defaultValue={selected.planned_budget}
                />
              </div>
              <div className="field">
                <label>Budget mode</label>
                <select name="budget_mode" defaultValue={selected.budget_mode}>
                  <option value="lifetime">Lifetime</option>
                  <option value="daily">Daily guidance</option>
                </select>
              </div>
              <div className="field">
                <label>Start</label>
                <input
                  name="start_date"
                  type="date"
                  defaultValue={selected.start_date || ""}
                />
              </div>
              <div className="field">
                <label>End</label>
                <input
                  name="end_date"
                  type="date"
                  defaultValue={selected.end_date || ""}
                />
              </div>
              <div className="field full">
                <label>Offer</label>
                <textarea
                  name="offer"
                  maxLength={2000}
                  defaultValue={selected.offer || ""}
                />
              </div>
              <div className="field full">
                <label>Description</label>
                <textarea
                  name="description"
                  maxLength={5000}
                  defaultValue={selected.description || ""}
                />
              </div>
            </div>
            <div className="modal-foot">
              <Button type="button" onClick={() => setEditingCampaign(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                type="submit"
                disabled={update.isPending}
              >
                {update.isPending ? "Saving…" : "Save campaign"}
              </Button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}

const contentTransitions: Record<
  MarketingContentStatus,
  MarketingContentStatus[]
> = {
  draft: ["review", "archived"],
  review: ["draft", "approved", "archived"],
  approved: ["scheduled", "ready_to_publish", "archived"],
  scheduled: ["approved", "ready_to_publish", "archived"],
  ready_to_publish: ["archived"],
  archived: [],
};

export function SocialManagementPage() {
  const [location] = useLocation();
  const { activeBusinessId } = useBusiness();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<MarketingContentStatus | "">("");
  const [selected, setSelected] = useState<MarketingContent | null>(null);
  const [schedule, setSchedule] = useState<MarketingContent | null>(null);
  const [editing, setEditing] = useState(false);
  const [briefing, setBriefing] = useState(false);
  const [creativeProgress, setCreativeProgress] = useState<CreativeProgress>(null);
  const [calendarDays, setCalendarDays] = useState<1 | 7 | 30>(7);
  const [calendarChannel, setCalendarChannel] = useState<MarketingChannel | "">(
    "",
  );
  const [calendarCampaign, setCalendarCampaign] = useState("");
  const [page, setPage] = useState(1);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const calendarStart = useMemo(() => new Date().toISOString(), []);
  const calendarEnd = useMemo(
    () =>
      new Date(
        new Date(calendarStart).getTime() + calendarDays * 86400000,
      ).toISOString(),
    [calendarStart, calendarDays],
  );

  const content = useQuery({
    queryKey: ["marketing", activeBusinessId, "content", status, page],
    queryFn: ({ signal }) =>
      marketingApi.content.list(
        activeBusinessId,
        { page, pageSize: 24, status: status || undefined },
        {},
        signal,
      ),
    enabled: Boolean(activeBusinessId),
  });
  const calendar = useQuery({
    queryKey: [
      "marketing",
      activeBusinessId,
      "calendar",
      calendarStart,
      calendarDays,
      calendarChannel,
      calendarCampaign,
    ],
    queryFn: ({ signal }) =>
      marketingApi.calendar.list(
        activeBusinessId,
        calendarStart,
        calendarEnd,
        {
          channel: calendarChannel || undefined,
          campaignId: calendarCampaign || undefined,
        },
        signal,
      ),
    enabled: Boolean(activeBusinessId),
  });
  const campaigns = useQuery({
    queryKey: ["marketing", activeBusinessId, "campaigns", "social-filter"],
    queryFn: ({ signal }) =>
      marketingApi.campaigns.list(activeBusinessId, { pageSize: 100 }, signal),
    enabled: Boolean(activeBusinessId),
  });
  const integrationRegistry = useQuery({
    queryKey: ["integrations", activeBusinessId, "registry", "social"],
    queryFn: ({ signal }) => integrationsApi.registry(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
  });
  const integrationConnections = useQuery({
    queryKey: ["integrations", activeBusinessId, "connections", "social"],
    queryFn: ({ signal }) => integrationsApi.connections(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
  });
  const versions = useQuery({
    queryKey: [
      "marketing",
      activeBusinessId,
      "content-versions",
      selected?.root_content_id,
    ],
    queryFn: ({ signal }) =>
      marketingApi.content.versions(activeBusinessId, selected!.id, signal),
    enabled: Boolean(activeBusinessId && selected),
  });
  const assets = useQuery({
    queryKey: ["marketing", activeBusinessId, "creative-assets", selected?.id],
    queryFn: ({ signal }) =>
      marketingApi.creative.list(
        activeBusinessId,
        selected?.campaign_id || undefined,
        selected!.id,
        signal,
      ),
    enabled: Boolean(activeBusinessId && selected),
  });
  const contentPlan = useQuery({
    queryKey: ["marketing", activeBusinessId, "automation", "content_plan"],
    queryFn: ({ signal }) =>
      marketingApi.automation.get(activeBusinessId, "content_plan", signal),
    enabled: Boolean(activeBusinessId),
  });
  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: ["marketing", activeBusinessId],
    });
  const refreshCreatives = () => queryClient.invalidateQueries({
    queryKey: ["marketing", activeBusinessId, "creative-assets"],
  });
  const move = useMutation({
    mutationFn: ({
      id,
      status: next,
    }: {
      id: string;
      status: MarketingContentStatus;
    }) => marketingApi.content.status(activeBusinessId, id, next),
    onSuccess: (item) => {
      setSelected(item);
      setNotice(`Content is now ${item.status.replaceAll("_", " ")}.`);
      setError("");
      void invalidate();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(reason, "Content status could not be changed."),
      ),
  });
  const edit = useMutation({
    mutationFn: (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      return marketingApi.content.edit(activeBusinessId, selected!.id, {
        title: String(form.get("title")),
        body: String(form.get("body")),
        cta: String(form.get("cta")) || null,
      });
    },
    onSuccess: (item) => {
      setSelected(item);
      setEditing(false);
      setNotice(
        `Version ${item.version} was saved; earlier versions remain available.`,
      );
      setError("");
      void invalidate();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(reason, "A new content version could not be saved."),
      ),
  });
  const createSchedule = useMutation({
    mutationFn: (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const value = new FormData(event.currentTarget).get("scheduled_for");
      return marketingApi.calendar.create(
        activeBusinessId,
        schedule!.id,
        new Date(String(value)).toISOString(),
      );
    },
    onSuccess: () => {
      setSchedule(null);
      setNotice(
        "Content was added to the internal calendar. No platform was called.",
      );
      setError("");
      void invalidate();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(
          reason,
          "Content could not be scheduled. Approve it first.",
        ),
      ),
  });
  const reschedule = useMutation({
    mutationFn: ({ item, value }: { item: SocialSchedule; value: string }) =>
      marketingApi.calendar.reschedule(
        activeBusinessId,
        item.id,
        new Date(value).toISOString(),
      ),
    onSuccess: () => {
      setNotice("Internal calendar item was rescheduled.");
      void invalidate();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(reason, "Calendar item could not be rescheduled."),
      ),
  });
  const unschedule = useMutation({
    mutationFn: (item: SocialSchedule) =>
      marketingApi.calendar.unschedule(activeBusinessId, item.id),
    onSuccess: () => {
      setNotice("Content was removed from the internal calendar.");
      void invalidate();
    },
    onError: (reason) =>
      setError(humanizeApiError(reason, "Calendar item could not be removed.")),
  });
  const createBrief = useMutation({
    mutationFn: (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const width = String(form.get("width") || "");
      const height = String(form.get("height") || "");
      return marketingApi.creative.brief(activeBusinessId, {
        campaign_id: selected?.campaign_id || null,
        content_id: selected!.id,
        asset_type: String(form.get("asset_type")),
        instructions: String(form.get("instructions")),
        aspect_ratio: String(form.get("aspect_ratio")) || null,
        width: width ? Number(width) : null,
        height: height ? Number(height) : null,
        alt_text: String(form.get("alt_text")) || null,
      });
    },
    onSuccess: () => {
      setBriefing(false);
      setNotice(
        "Creative strategy was saved and is ready for visual generation.",
      );
      setError("");
    },
    onError: () => setError("The creative strategy could not be prepared. Refresh to see saved progress before retrying."),
    onSettled: () => refreshCreatives(),
  });
  const generateVisual = useMutation({
    mutationFn: (asset: CreativeAsset) => runCreativeOperationWithRecovery({
      progress: { phase: "visual", contentId: asset.content_id || undefined, assetId: asset.id },
      operation: () => marketingApi.creative.generate(activeBusinessId, asset.id),
      refresh: refreshCreatives,
      onProgress: setCreativeProgress,
    }),
    onSuccess: (asset) => {
      setNotice(creativeResultNotice(asset));
      setError("");
    },
    onError: () => setError("The final creative could not be completed. Refresh to see saved progress and try again."),
  });
  const regenerateVisual = useMutation({
    mutationFn: (asset: CreativeAsset) => runCreativeOperationWithRecovery({
      progress: { phase: "visual", contentId: asset.content_id || undefined, assetId: asset.id },
      operation: () => marketingApi.creative.regenerate(activeBusinessId, asset.id),
      refresh: refreshCreatives,
      onProgress: setCreativeProgress,
    }),
    onSuccess: (asset) => {
      setNotice(
        asset.generation_status === "ready"
          ? "A new final creative is ready. Previous artwork remains in history."
          : `${creativeResultNotice(asset)} The new revision remains in history.`,
      );
      setError("");
    },
    onError: () => setError("A new creative version could not be completed. Refresh to see any saved history before retrying."),
  });
  const regenerate = useMutation({
    mutationFn: (item: MarketingContent) =>
      marketingApi.content.generate(activeBusinessId, {
        prompt: `Create a distinct, fact-grounded variant of this internal marketing draft: ${item.body}`,
        campaign_id: item.campaign_id,
        channel: item.channel,
        content_type: item.content_type,
        title: item.title,
        language: item.language,
        parent_content_id: item.id,
      }),
    onSuccess: (item) => {
      setSelected(item);
      setNotice(`AI-generated version ${item.version} is ready for review.`);
      setError("");
      void invalidate();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(
          reason,
          "A regenerated content version could not be created.",
        ),
      ),
  });
  const refreshContentPlan = useMutation({
    mutationFn: () =>
      marketingApi.automation.refresh(activeBusinessId, "content_plan"),
    onSuccess: (run) => {
      setNotice(
        `Weekly content planning ${run.status.replaceAll("_", " ")}. Existing proposal windows are reused.`,
      );
      setError("");
      void invalidate();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(
          reason,
          "The bounded content plan could not be scheduled.",
        ),
      ),
  });
  const preparePublish = useMutation({
    mutationFn: (item: MarketingContent) =>
      marketingApi.content.preparePublish(
        activeBusinessId,
        item.id,
        item.channel as "facebook" | "instagram",
      ),
    onSuccess: (proposal) => {
      setNotice(
        `${proposal.action_status.replaceAll("_", " ")}: ${proposal.connector_message}`,
      );
      setError("");
      void invalidate();
    },
    onError: (reason) =>
      setError(
        humanizeApiError(
          reason,
          "A governed publish action could not be prepared.",
        ),
      ),
  });
  const byId = new Map(content.data?.items.map((item) => [item.id, item]));
  const selectedConnector = integrationRegistry.data?.find(
    (item) => item.connector_type === selected?.channel,
  );
  const selectedConnection = integrationConnections.data?.find(
    (item) => item.connector_type === selected?.channel,
  );
  const selectedProviderWriteReady = Boolean(
    selectedConnector?.external_writes_enabled &&
      selectedConnector.setup_status === "available" &&
      selectedConnection?.status === "connected" &&
      selectedConnection.authentication_state === "authorized" &&
      selectedConnection.health === "healthy",
  );
  const selectedProviderCopy = integrationRegistry.isPending ||
    integrationConnections.isPending
    ? "Provider write readiness is still being checked."
    : integrationRegistry.isError || integrationConnections.isError
      ? "Provider write readiness could not be verified."
      : selectedProviderWriteReady
        ? "A healthy authenticated write provider is available; approval is still mandatory."
        : "A healthy authenticated provider with accepted write capability is still required.";
  const creativePhase = creativePhaseForDisplay(
    creativeProgress,
    selected?.id,
    assets.data?.[0]?.id,
  );
  const selectedCreativeFormat = selected
    ? creativeFormatForContent(selected)
    : creativeFormatForContent({
        channel: "instagram",
        content_type: "social_post",
      });

  return (
    <>
      <PageHeader
        eyebrow="AI CMO · Content autopilot"
        title="Content Studio & Social Calendar"
        subtitle="Review AI recommendations, upcoming drafts, approval state, calendar slots, and measured results."
        actionClassName="cmo-content-header-actions"
        action={
          <Link
            href="/marketing/content"
            className="btn btn-secondary cmo-compact-action"
            data-testid="button-advanced-create-draft"
          >
            <Plus />
            <span>Create draft</span>
            <span className="cmo-action-context">Advanced</span>
          </Link>
        }
      />
      <CmoDepartmentNav
        active={
          location.endsWith("/content")
            ? "Content"
            : location.endsWith("/calendar")
              ? "Calendar"
              : "Social"
        }
      />
      {notice && (
        <div className="ai-banner">
          <Check /> {notice}
          <button className="close-btn" onClick={() => setNotice("")}>
            <X />
          </button>
        </div>
      )}
      {error && (
        <div className="ai-banner">
          <AlertCircle /> {error}
          <button className="close-btn" onClick={() => setError("")}>
            <X />
          </button>
        </div>
      )}
      <Card className="intelligence-hero">
        <div className="intelligence-hero-icon">
          <Wand2 />
        </div>
        <div className="row-main">
          <div className="eyebrow">Weekly content plan</div>
          <h2>
            {contentPlan.data
              ? `${contentPlan.data.proposal_count} draft proposal${contentPlan.data.proposal_count === 1 ? "" : "s"} prepared in this window`
              : "AI fills missing recommended slots with bounded drafts"}
          </h2>
          <p className="subtle">
            {contentPlan.data
              ? `Latest run is ${contentPlan.data.status.replaceAll("_", " ")}. Proposals are never marked published.`
              : "No completed plan yet. If AI configuration is missing, this section reports configuration required without affecting stored content."}
          </p>
        </div>
        <Button
          variant="green"
          disabled={refreshContentPlan.isPending}
          onClick={() => refreshContentPlan.mutate()}
        >
          <Sparkles />{" "}
          {refreshContentPlan.isPending
            ? "Scheduling…"
            : "Refresh content plan"}
        </Button>
      </Card>
      <div className="social-channel-strip">
        {["instagram", "facebook", "linkedin", "tiktok"].map((platform) => {
          const Icon =
            platformIcons[platform as keyof typeof platformIcons] ?? Sparkles;
          const definition = integrationRegistry.data?.find(
            (item) => item.connector_type === platform,
          );
          const connection = integrationConnections.data?.find(
            (item) => item.connector_type === platform,
          );
          const healthy =
            connection?.status === "connected" &&
            connection.authentication_state === "authorized" &&
            connection.health === "healthy";
          const needsAttention = Boolean(
            connection &&
              ["degraded", "reauth_required", "revoked"].includes(
                connection.status,
              ),
          );
          const writeReady = Boolean(
            healthy &&
              definition?.external_writes_enabled &&
              definition.setup_status === "available",
          );
          const copy = healthy
            ? writeReady
              ? "Connected and healthy; governed publish preparation is available"
              : "Connected and healthy; external publishing remains provider-disabled"
            : needsAttention
              ? "Connection needs attention before publishing can be prepared"
              : definition
                ? `${definition.setup_status.replaceAll("_", " ")} · planning remains available`
                : "No connector is registered; internal planning only";
          return (
            <Card key={platform} className="social-channel">
              <Icon />
              <div className="row-main">
                <strong>{platform}</strong>
                <span>{copy}</span>
              </div>
              <Badge tone={writeReady ? "success" : needsAttention ? "danger" : healthy ? "warning" : "neutral"}>
                {healthy
                  ? writeReady
                    ? "Write ready"
                    : "Publish disabled"
                  : needsAttention
                    ? "Needs attention"
                    : definition?.setup_status.replaceAll("_", " ") || "Planning only"}
              </Badge>
            </Card>
          );
        })}
      </div>
      <div className="tabs">
        {[
          "",
          "draft",
          "review",
          "approved",
          "scheduled",
          "ready_to_publish",
          "archived",
        ].map((item) => (
          <button
            className={`tab ${status === item ? "active" : ""}`}
            onClick={() => {
              setStatus(item as MarketingContentStatus | "");
              setPage(1);
            }}
            key={item || "all"}
          >
            {item ? item.replaceAll("_", " ") : "All"}
          </button>
        ))}
      </div>
      {content.isError ? (
        <ErrorCard error={content.error} retry={() => void content.refetch()} />
      ) : (
        <>
          <div className="grid social-post-grid">
            {content.data?.items.map((post) => {
              const Icon =
                platformIcons[post.channel as keyof typeof platformIcons] ??
                Sparkles;
              const publishableChannel = ["facebook", "instagram"].includes(
                post.channel,
              );
              return (
                <Card className="social-post" key={post.id}>
                  <div className="social-post-head">
                    <span className="platform-icon">
                      <Icon />
                    </span>
                    <div>
                      <strong>{post.channel}</strong>
                      <div className="row-copy">
                        {post.ai_generated
                          ? "AI recommended"
                          : "Manual fallback"}{" "}
                        · {post.content_type.replaceAll("_", " ")} · version{" "}
                        {post.version}
                      </div>
                    </div>
                    <Badge
                      tone={
                        post.status === "approved" ||
                        post.status === "ready_to_publish"
                          ? "success"
                          : post.status === "review"
                            ? "warning"
                            : "neutral"
                      }
                    >
                      {post.status.replaceAll("_", " ")}
                    </Badge>
                  </div>
                  <h3>{post.title}</h3>
                  <p className="social-copy">{post.body}</p>
                  {post.recommended_for && (
                    <div className="row-copy">
                      Recommended for · {post.recommended_for}
                    </div>
                  )}
                  {post.cta && (
                    <div className="recommendation-strip">
                      <Target />
                      <div>
                        <div className="eyebrow">CTA</div>
                        <p>{post.cta}</p>
                      </div>
                    </div>
                  )}
                  <div className="toolbar">
                    <Button
                      variant="soft"
                      className="btn-sm"
                      onClick={() => setSelected(post)}
                    >
                      <Eye /> Review
                    </Button>
                    {post.status === "approved" && (
                      <Button
                        variant="green"
                        className="btn-sm"
                        onClick={() => setSchedule(post)}
                      >
                        <Calendar /> Schedule
                      </Button>
                    )}
                    {publishableChannel &&
                      ["approved", "scheduled", "ready_to_publish"].includes(
                        post.status,
                      ) && (
                        <Button
                          variant="primary"
                          className="btn-sm"
                          disabled={preparePublish.isPending}
                          onClick={() => preparePublish.mutate(post)}
                        >
                          <Send /> Prepare governed publish
                        </Button>
                      )}
                  </div>
                </Card>
              );
            })}
            {content.isLoading && (
              <Card>
                <div className="empty">
                  <p>Loading content…</p>
                </div>
              </Card>
            )}
            {content.data && !content.data.items.length && (
              <EmptyCard
                icon={<Wand2 />}
                title="No content in this view"
                copy="Run the bounded weekly content plan. It creates only provider-grounded proposals and never publishes automatically."
              />
            )}
          </div>
          {content.data && (
            <Pager
              page={page}
              pageSize={content.data.page_size}
              total={content.data.total}
              onPage={setPage}
            />
          )}
        </>
      )}
      <Card style={{ marginTop: 20 }}>
        <SectionTitle
          title="Upcoming internal calendar"
          action={
            <div className="toolbar">
              <div className="tabs">
                {(
                  [
                    [1, "Day"],
                    [7, "Week"],
                    [30, "Month"],
                  ] as const
                ).map(([days, label]) => (
                  <button
                    className={`tab ${calendarDays === days ? "active" : ""}`}
                    key={days}
                    onClick={() => setCalendarDays(days)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <Badge>{calendar.data?.length ?? 0} scheduled</Badge>
            </div>
          }
        />
        <div className="table-toolbar">
          <div className="toolbar">
            <select
              aria-label="Filter calendar by channel"
              value={calendarChannel}
              onChange={(event) =>
                setCalendarChannel(event.target.value as MarketingChannel | "")
              }
            >
              <option value="">All channels</option>
              {channels.map((channel) => (
                <option value={channel} key={channel}>
                  {channel.replaceAll("_", " ")}
                </option>
              ))}
            </select>
            <select
              aria-label="Filter calendar by campaign"
              value={calendarCampaign}
              onChange={(event) => setCalendarCampaign(event.target.value)}
            >
              <option value="">All campaigns</option>
              {campaigns.data?.items.map((campaign) => (
                <option value={campaign.id} key={campaign.id}>
                  {campaign.name}
                </option>
              ))}
            </select>
          </div>
          <span className="subtle">
            Internal schedule · {new Date(calendarStart).toLocaleDateString()}{" "}
            onward
          </span>
        </div>
        {calendar.isError ? (
          <ErrorCard
            error={calendar.error}
            retry={() => void calendar.refetch()}
          />
        ) : (
          calendar.data?.map((item) => (
            <div className="list-row" key={item.id}>
              <Calendar />
              <div className="row-main">
                <strong>
                  {byId.get(item.content_id)?.title ||
                    `${item.channel} content`}
                </strong>
                <div className="row-copy">
                  {new Date(item.scheduled_for).toLocaleString()} ·{" "}
                  {item.timezone} · {item.status.replaceAll("_", " ")}
                </div>
              </div>
              <input
                aria-label="Reschedule date"
                type="datetime-local"
                defaultValue={new Date(item.scheduled_for)
                  .toISOString()
                  .slice(0, 16)}
                onChange={(event) =>
                  event.target.value &&
                  reschedule.mutate({ item, value: event.target.value })
                }
              />
              <Button
                className="btn-sm"
                onClick={() => unschedule.mutate(item)}
                disabled={unschedule.isPending}
              >
                Unschedule
              </Button>
            </div>
          ))
        )}
        {calendar.isLoading && <p className="subtle">Loading calendar…</p>}
        {calendar.data && !calendar.data.length && (
          <div className="empty">
            <Calendar />
            <h3>
              No scheduled content in this{" "}
              {calendarDays === 1
                ? "day"
                : calendarDays === 7
                  ? "week"
                  : "month"}
            </h3>
            <p>Approve content, then choose a real date and time.</p>
          </div>
        )}
      </Card>
      {selected && (
        <Modal
          wide
          title={selected.title}
          description={`${selected.channel} · ${selected.status.replaceAll("_", " ")} · version ${selected.version}`}
          onClose={() => {
            setSelected(null);
            setEditing(false);
            setBriefing(false);
          }}
        >
          <div className="social-preview">
            <div className="social-preview-brand">
              <span className="platform-icon">
                <Sparkles />
              </span>
              <div>
                <strong>Your business</strong>
                <span>
                  {selected.ai_generated
                    ? "AI CMO draft"
                    : "User-authored draft"}
                </span>
              </div>
            </div>
            <p>{selected.body}</p>
          </div>
          {selected.cta && (
            <Card>
              <div className="eyebrow">Call to action</div>
              <p>{selected.cta}</p>
            </Card>
          )}
          <SectionTitle
            title="Version history"
            action={<Badge>{versions.data?.length ?? 0} version(s)</Badge>}
          />
          {versions.isLoading && (
            <p className="subtle">Loading version history…</p>
          )}
          {versions.data?.map((version) => (
            <button
              className="list-row"
              key={version.id}
              onClick={() => setSelected(version)}
            >
              <Copy />
              <div className="row-main">
                <strong>
                  Version {version.version} · {version.title}
                </strong>
                <div className="row-copy">
                  {version.ai_generated ? "AI-generated" : "User-authored"} ·{" "}
                  {new Date(version.created_at).toLocaleString()}
                </div>
              </div>
              <Badge tone={version.id === selected.id ? "success" : "neutral"}>
                {version.status.replaceAll("_", " ")}
              </Badge>
            </button>
          ))}
          <SectionTitle
            title="Creative assets"
            action={
              assets.data?.length ? (
                <Button className="btn-sm" onClick={() => setBriefing(true)}>
                  <Sparkles /> Create another brief
                </Button>
              ) : undefined
            }
          />
          <CmoCreativePanel
            creative={assets.data?.[0]}
            isLoading={assets.isLoading}
            error={assets.isError ? humanizeApiError(assets.error, "Retry loading creative history.") : null}
            phase={creativePhase}
            onCreate={() => setBriefing(true)}
            onReload={() => void assets.refetch()}
            onRetry={(asset) => generateVisual.mutate(asset)}
            onRegenerate={(asset) => regenerateVisual.mutate(asset)}
          />
          {assets.data?.slice(1).map((asset) => (
            <div className="list-row" key={asset.id}>
              <Sparkles />
              <div className="row-main">
                <strong>{asset.asset_type.replaceAll("_", " ")}</strong>
                <div className="row-copy">
                  {asset.visual_direction ||
                    asset.instructions ||
                    "Creative brief"}
                </div>
              </div>
              <Badge
                tone={
                  asset.generation_status === "brief_ready"
                    ? "success"
                    : "warning"
                }
              >
                {asset.generation_status.replaceAll("_", " ")}
              </Badge>
            </div>
          ))}
          {assets.data && !assets.data.length && (
            <p className="subtle">
              No creative brief or asset is attached to this version.
            </p>
          )}
          <div className="toolbar" style={{ marginTop: 18 }}>
            <Button onClick={() => setEditing(true)}>
              <Copy /> Create edited version
            </Button>
            <Button
              disabled={regenerate.isPending}
              onClick={() => regenerate.mutate(selected)}
            >
              <Wand2 />{" "}
              {regenerate.isPending ? "Regenerating…" : "Regenerate version"}
            </Button>
            {contentTransitions[selected.status].map((next) => (
              <Button
                key={next}
                variant={next === "archived" ? "danger" : "green"}
                disabled={move.isPending}
                onClick={() => move.mutate({ id: selected.id, status: next })}
              >
                {next === "review" ? <Send /> : <Check />}{" "}
                {next.replaceAll("_", " ")}
              </Button>
            ))}
            {selected.status === "approved" && (
              <Button variant="primary" onClick={() => setSchedule(selected)}>
                <Calendar /> Schedule
              </Button>
            )}
            {["facebook", "instagram"].includes(selected.channel) &&
              ["approved", "scheduled", "ready_to_publish"].includes(
                selected.status,
              ) && (
                <Button
                  variant="primary"
                  disabled={preparePublish.isPending}
                  onClick={() => preparePublish.mutate(selected)}
                >
                  <Send /> Prepare governed publish
                </Button>
              )}
          </div>
          <div className="ai-banner">
            <AlertCircle /> Visual creation and external publishing remain separate.
            Publishing always requires your approval and an available connected channel. {selectedProviderCopy}
          </div>
        </Modal>
      )}
      {editing && selected && (
        <Modal
          title="Create content version"
          description="The current version remains immutable in history."
          onClose={() => setEditing(false)}
        >
          <form onSubmit={(event) => edit.mutate(event)}>
            <div className="form-grid">
              <div className="field full">
                <label>Title</label>
                <input name="title" required defaultValue={selected.title} />
              </div>
              <div className="field full">
                <label>Copy</label>
                <textarea
                  name="body"
                  required
                  maxLength={20000}
                  defaultValue={selected.body}
                />
              </div>
              <div className="field full">
                <label>CTA</label>
                <input
                  name="cta"
                  maxLength={300}
                  defaultValue={selected.cta || ""}
                />
              </div>
            </div>
            <div className="modal-foot">
              <Button type="button" onClick={() => setEditing(false)}>
                Cancel
              </Button>
              <Button variant="primary" type="submit" disabled={edit.isPending}>
                {edit.isPending ? "Saving…" : "Save new version"}
              </Button>
            </div>
          </form>
        </Modal>
      )}
      {briefing && selected && (
        <Modal
          title="Create creative brief"
          description="Prepare grounded visual direction first, then generate a deterministic final branded creative."
          onClose={() => setBriefing(false)}
        >
          <form onSubmit={(event) => createBrief.mutate(event)}>
            <div className="form-grid">
              <div className="field">
                <label>Asset type</label>
                <select name="asset_type" defaultValue={selectedCreativeFormat.asset_type}>
                  <option value="social_square">Social square</option>
                  <option value="story_reel">Story / reel</option>
                  <option value="landscape_ad">Landscape ad</option>
                  <option value="display_banner">Display banner</option>
                  <option value="creative_brief">Creative brief only</option>
                  <option value="other">Other</option>
                </select>
              </div>
              <div className="field">
                <label>Aspect ratio</label>
                <input name="aspect_ratio" maxLength={16} defaultValue={selectedCreativeFormat.aspect_ratio} />
              </div>
              <div className="field full">
                <label>Visual instructions</label>
                <textarea
                  name="instructions"
                  required
                  maxLength={5000}
                  placeholder="Describe composition, brand treatment, subject, and constraints using trusted product facts."
                />
              </div>
              <div className="field">
                <label>Width</label>
                <input
                  name="width"
                  type="number"
                  min="1"
                  max="20000"
                  defaultValue={selectedCreativeFormat.width}
                />
              </div>
              <div className="field">
                <label>Height</label>
                <input
                  name="height"
                  type="number"
                  min="1"
                  max="20000"
                  defaultValue={selectedCreativeFormat.height}
                />
              </div>
              <div className="field full">
                <label>Alt text</label>
                <textarea name="alt_text" maxLength={1000} />
              </div>
            </div>
            <div className="modal-foot">
              <Button type="button" onClick={() => setBriefing(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                type="submit"
                disabled={createBrief.isPending}
              >
                <Sparkles />{" "}
                {createBrief.isPending ? "Preparing…" : "Save creative brief"}
              </Button>
            </div>
          </form>
        </Modal>
      )}
      {schedule && (
        <Modal
          title="Schedule content"
          description="This creates an internal calendar record only."
          onClose={() => setSchedule(null)}
        >
          <form onSubmit={(event) => createSchedule.mutate(event)}>
            <div className="field">
              <label>Date and time</label>
              <input name="scheduled_for" type="datetime-local" required />
            </div>
            <div className="modal-foot">
              <Button type="button" onClick={() => setSchedule(null)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                type="submit"
                disabled={createSchedule.isPending}
              >
                <Calendar /> Schedule internally
              </Button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}
