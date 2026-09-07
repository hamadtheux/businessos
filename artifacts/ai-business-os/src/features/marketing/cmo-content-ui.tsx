import { useState, type ReactNode } from "react";
import {
  AlertCircle,
  Archive,
  Calendar,
  ChevronDown,
  Eye,
  FileText,
  Image as ImageIcon,
  MoreHorizontal,
  Pencil,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
  Video,
} from "lucide-react";

import { Badge, Button, Card, EmptyState, SectionTitle } from "@/components/product-ui";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  safeCreativeMediaUrl,
  type PublishingCapability,
} from "@/lib/cmo-ux";
import type {
  CreativeAsset,
  MarketingContent,
  MarketingContentStatus,
  SocialSchedule,
} from "@/services/api-types";

export function readableContentValue(value: string) {
  return value.replaceAll("_", " ");
}

export function contentStatusTone(
  status: MarketingContentStatus,
): "success" | "warning" | "info" | "neutral" {
  if (["approved", "scheduled", "ready_to_publish"].includes(status)) {
    return "success";
  }
  if (status === "review") return "info";
  if (status === "draft") return "warning";
  return "neutral";
}

export function ContentStatusBadge({ status }: { status: MarketingContentStatus }) {
  return (
    <Badge tone={contentStatusTone(status)} className="cmo-content-status">
      {readableContentValue(status)}
    </Badge>
  );
}

type CreativeMediaPresentation = {
  label: string;
  detail: string;
  tone: "success" | "warning" | "info" | "neutral";
  active: boolean;
};

export function creativeMediaPresentation(
  creative: CreativeAsset,
): CreativeMediaPresentation {
  const media = creative.media_type === "video" ? "Video" : "Image";

  switch (creative.generation_status) {
    case "ready":
      return {
        label: `${media} ready`,
        detail: "Final creative ready to review",
        tone: "success",
        active: false,
      };
    case "provider_required":
      return {
        label: `${media} unavailable`,
        detail: "Creative strategy saved for retry",
        tone: "warning",
        active: false,
      };
    case "failed":
      return {
        label: `${media} needs retry`,
        detail: "Saved direction is still available",
        tone: "warning",
        active: false,
      };
    case "queued":
      return {
        label: `${media} queued`,
        detail: "Waiting to start",
        tone: "info",
        active: true,
      };
    case "generating":
      return {
        label: `Generating ${media.toLowerCase()}`,
        detail: "Creative generation is in progress",
        tone: "info",
        active: true,
      };
    case "reviewing":
      return {
        label: `Reviewing ${media.toLowerCase()}`,
        detail: "Checking creative quality",
        tone: "info",
        active: true,
      };
    case "repairing":
      return {
        label: `Refining ${media.toLowerCase()}`,
        detail: "Improving creative quality",
        tone: "info",
        active: true,
      };
    case "brief_ready":
    case "strategy_ready":
      return {
        label: `${media} strategy ready`,
        detail: "Ready for generation",
        tone: "info",
        active: false,
      };
    case "archived":
      return {
        label: `${media} archived`,
        detail: "Available in creative history",
        tone: "neutral",
        active: false,
      };
    default:
      return {
        label: `${media} draft`,
        detail: "Creative direction is not final",
        tone: "neutral",
        active: false,
      };
  }
}

function capabilitySummary(capability: PublishingCapability) {
  switch (capability.state) {
    case "ready":
      return "Ready after content approval.";
    case "checking":
      return "Checking publishing readiness…";
    case "unverified":
      return "Readiness unavailable. Scheduling still works.";
    case "unavailable":
      return "Connect an authorized provider to publish.";
    default:
      return "Plan and schedule content here.";
  }
}

export function ChannelCapabilityCard({
  icon,
  label,
  status,
  capability,
  tone = "neutral",
}: {
  icon: ReactNode;
  label: string;
  status: string;
  capability: PublishingCapability;
  tone?: "success" | "warning" | "danger" | "neutral";
}) {
  return (
    <Card className="social-channel">
      <span className="social-channel-icon" aria-hidden="true">{icon}</span>
      <div className="row-main">
        <strong>{label}</strong>
        <span>{capabilitySummary(capability)}</span>
      </div>
      <Badge tone={tone}>{status}</Badge>
    </Card>
  );
}

function stopCardAction(event: React.SyntheticEvent) {
  event.stopPropagation();
}

function ContentMediaPreview({ creative }: { creative?: CreativeAsset }) {
  const [failedPreviewKey, setFailedPreviewKey] = useState<string | null>(null);

  if (!creative) return null;

  const reference = safeCreativeMediaUrl(creative?.storage_reference);
  const previewKey = `${creative.id}:${reference ?? "missing"}`;
  const presentation = creativeMediaPresentation(creative);
  const previewAvailable =
    creative.generation_status === "ready" &&
    Boolean(reference) &&
    failedPreviewKey !== previewKey;

  if (!previewAvailable) {
    const previewMissing = creative.generation_status === "ready";
    const MediaIcon = previewMissing
      ? AlertCircle
      : creative.media_type === "video"
        ? Video
        : ImageIcon;

    return (
      <div
        className="cmo-content-card-media cmo-content-card-media-state"
        data-state={previewMissing ? "preview_unavailable" : creative.generation_status}
        role={presentation.active ? "status" : undefined}
        aria-live={presentation.active ? "polite" : undefined}
      >
        <MediaIcon aria-hidden="true" />
        <div>
          <strong>{previewMissing ? "Preview unavailable" : presentation.label}</strong>
          <small>{previewMissing ? "Open details to review the saved creative" : presentation.detail}</small>
        </div>
      </div>
    );
  }

  return (
    <div className="cmo-content-card-media" aria-label={`${creative.media_type} creative ready`}>
      {creative.media_type === "video" ? (
        <video
          src={reference!}
          muted
          playsInline
          preload="metadata"
          onError={() => setFailedPreviewKey(previewKey)}
        />
      ) : (
        <img
          src={reference!}
          alt={creative.alt_text || "Marketing creative preview"}
          loading="lazy"
          onError={() => setFailedPreviewKey(previewKey)}
        />
      )}
      <span>{creative.media_type === "video" ? <Video /> : <ImageIcon />} Creative ready</span>
    </div>
  );
}

function CardPrimaryAction({
  content,
  capability,
  busy,
  onOpen,
  onSchedule,
  onPreparePublish,
}: {
  content: MarketingContent;
  capability: PublishingCapability;
  busy: boolean;
  onOpen: () => void;
  onSchedule: () => void;
  onPreparePublish: () => void;
}) {
  if (content.status === "draft") {
    return <Button variant="primary" onClick={onOpen} disabled={busy}><Eye /> Review draft</Button>;
  }
  if (content.status === "review") {
    return <Button variant="primary" onClick={onOpen} disabled={busy}><Eye /> Review & approve</Button>;
  }
  if (content.status === "approved" && capability.canPrepare) {
    return <Button variant="primary" onClick={onPreparePublish} disabled={busy}><Send /> Prepare publish</Button>;
  }
  if (content.status === "approved") {
    return <Button variant="primary" onClick={onSchedule} disabled={busy}><Calendar /> Schedule</Button>;
  }
  if (["scheduled", "ready_to_publish"].includes(content.status) && capability.canPrepare) {
    return <Button variant="primary" onClick={onPreparePublish} disabled={busy}><Send /> Prepare publish</Button>;
  }
  return <Button variant="primary" onClick={onOpen} disabled={busy}><Eye /> Open details</Button>;
}

export function ContentLibraryCard({
  content,
  icon,
  creative,
  schedule,
  capability,
  busy = false,
  onOpen,
  onEdit,
  onRegenerate,
  onSchedule,
  onPreparePublish,
  onArchive,
}: {
  content: MarketingContent;
  icon: ReactNode;
  creative?: CreativeAsset;
  schedule?: SocialSchedule;
  capability: PublishingCapability;
  busy?: boolean;
  onOpen: () => void;
  onEdit: () => void;
  onRegenerate: () => void;
  onSchedule: () => void;
  onPreparePublish: () => void;
  onArchive?: () => void;
}) {
  const handleOpen = () => !busy && onOpen();
  const scheduledCopy = schedule
    ? new Date(schedule.scheduled_for).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : null;
  const creativeState = creative ? creativeMediaPresentation(creative) : null;

  return (
    <Card className="social-post cmo-content-card" pad={false}>
      <button
        type="button"
        className="cmo-content-card-open"
        onClick={handleOpen}
        disabled={busy}
        aria-label={`Open ${content.title}`}
      >
        <span className="social-post-head">
          <span className="platform-icon" aria-hidden="true">{icon}</span>
          <span className="cmo-content-card-channel">
            <strong>{readableContentValue(content.channel)}</strong>
            <span>{content.ai_generated ? "AI CMO" : "Manual"} · Version {content.version}</span>
          </span>
          <ContentStatusBadge status={content.status} />
        </span>

        <span className={`cmo-content-card-main ${creative ? "has-media" : ""}`}>
          <span className="cmo-content-card-copy">
            <span className="cmo-content-card-title">{content.title}</span>
            <span className="social-copy">{content.body}</span>
          </span>
          <ContentMediaPreview creative={creative} />
        </span>

        <span className="cmo-content-card-meta" aria-label="Content details">
          <span><FileText /> {readableContentValue(content.content_type)}</span>
          {creative && creativeState && <span>{creative.media_type === "video" ? <Video /> : <ImageIcon />} {creativeState.label}</span>}
          {scheduledCopy && <span><Calendar /> {scheduledCopy}</span>}
          {content.cta && <span className="cmo-content-card-cta">CTA · {content.cta}</span>}
        </span>
      </button>

      <div className="cmo-content-card-actions" onClick={stopCardAction}>
        <CardPrimaryAction
          content={content}
          capability={capability}
          busy={busy}
          onOpen={onOpen}
          onSchedule={onSchedule}
          onPreparePublish={onPreparePublish}
        />
        {content.status === "approved" && capability.canPrepare && (
          <Button variant="secondary" onClick={onSchedule} disabled={busy}><Calendar /> Schedule</Button>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="tertiary" className="cmo-content-card-more" aria-label={`More actions for ${content.title}`} disabled={busy}>
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="cmo-content-menu">
            <DropdownMenuItem onSelect={onOpen}><Eye /> Open details</DropdownMenuItem>
            <DropdownMenuItem onSelect={onEdit}><Pencil /> Edit content</DropdownMenuItem>
            <DropdownMenuItem onSelect={onRegenerate}><RefreshCw /> Regenerate copy</DropdownMenuItem>
            {onArchive && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={onArchive}><Archive /> Archive</DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </Card>
  );
}

function evidenceSummary(content: MarketingContent) {
  const evidence = content.source_evidence?.find(
    (item) => item.classification === "trusted_context_assembly",
  );
  const summary = evidence?.summary;
  return typeof summary === "string" && summary.trim() ? summary.trim() : null;
}

export function AIDetailsDisclosure({ content }: { content: MarketingContent }) {
  const sourceCount = content.source_evidence?.length ?? 0;
  const disclosureLabel = content.ai_generated
    ? "AI details & grounding"
    : "Content details & provenance";
  const DisclosureIcon = content.ai_generated ? Sparkles : FileText;
  return (
    <details className="cmo-ai-disclosure">
      <summary>
        <span><DisclosureIcon /> {disclosureLabel}</span>
        <span className="cmo-ai-disclosure-summary">
          {sourceCount ? `${sourceCount} source record${sourceCount === 1 ? "" : "s"}` : content.ai_generated ? "Generation details" : "Authorship details"}
          <ChevronDown aria-hidden="true" />
        </span>
      </summary>
      <div className="cmo-ai-detail-list">
        <div>
          <ShieldCheck />
          <div><strong>Grounding</strong><p>{evidenceSummary(content) ?? (content.ai_generated ? "Generated through the tenant-scoped AI CMO runtime." : "This is a manually authored content version.")}</p></div>
        </div>
        {content.recommended_for && (
          <div><Sparkles /><div><strong>Recommended for</strong><p>{content.recommended_for}</p></div></div>
        )}
        {content.creative_brief && (
          <div><ImageIcon /><div><strong>Creative direction</strong><p>{content.creative_brief}</p></div></div>
        )}
        {content.generation_reasoning && (
          <div><ShieldCheck /><div><strong>Why this direction</strong><p>{content.generation_reasoning}</p></div></div>
        )}
      </div>
    </details>
  );
}

export function CmoCalendarCompanion({
  items,
  content,
  isLoading = false,
  error,
  onRetry,
  onSchedule,
}: {
  items?: SocialSchedule[];
  content?: MarketingContent[];
  isLoading?: boolean;
  error?: string | null;
  onRetry: () => void;
  onSchedule?: (content: MarketingContent) => void;
}) {
  const approved = content?.find((item) => item.status === "approved");
  const byId = new Map(content?.map((item) => [item.id, item]));

  return (
    <Card className="cmo-calendar-companion">
      <SectionTitle title="Content calendar" action={<Badge>{items?.length ?? 0} upcoming</Badge>} />
      {error ? (
        <EmptyState
          compact
          icon={<Calendar />}
          title="Calendar could not load"
          description={error}
          action={<Button onClick={onRetry}><RefreshCw /> Retry calendar</Button>}
        />
      ) : isLoading ? (
        <div className="cmo-calendar-loading" role="status" aria-live="polite"><RefreshCw className="spin" /> Loading calendar…</div>
      ) : items?.length ? (
        <div className="cmo-calendar-list">
          {items.slice(0, 8).map((item) => {
            const contentItem = byId.get(item.content_id);
            const date = new Date(item.scheduled_for);
            return (
              <div className="cmo-calendar-item" key={item.id}>
                <time dateTime={item.scheduled_for}>
                  <strong>{date.toLocaleDateString(undefined, { day: "numeric" })}</strong>
                  <span>{date.toLocaleDateString(undefined, { month: "short" })}</span>
                </time>
                <div className="row-main">
                  <strong>{contentItem?.title || `${readableContentValue(item.channel)} content`}</strong>
                  <span>{date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })} · {readableContentValue(item.channel)}</span>
                </div>
                <Badge tone="success">{readableContentValue(item.status)}</Badge>
              </div>
            );
          })}
        </div>
      ) : (
        <EmptyState
          compact
          icon={<Calendar />}
          title="Your calendar is clear"
          description="Approved content can be scheduled here even before a publishing provider is connected."
          action={approved && onSchedule ? <Button variant="primary" onClick={() => onSchedule(approved)}><Calendar /> Schedule approved content</Button> : undefined}
        />
      )}
    </Card>
  );
}
