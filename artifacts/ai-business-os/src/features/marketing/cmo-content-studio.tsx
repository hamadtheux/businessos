import {
  AlertCircle,
  Calendar,
  Check,
  FileClock,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
  WandSparkles,
} from "lucide-react";

import {
  Badge,
  Button,
  Card,
  SectionTitle,
} from "@/components/product-ui";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  CmoCreativePanel,
  type CreativePhase,
  type CreativeVariationMode,
} from "@/features/marketing/cmo-creative-panel";
import {
  AIDetailsDisclosure,
  ContentStatusBadge,
  creativeMediaPresentation,
  readableContentValue,
} from "@/features/marketing/cmo-content-ui";
import type {
  CreativeMediaType,
  PublishingCapability,
} from "@/lib/cmo-ux";
import type {
  CreativeAsset,
  MarketingContent,
} from "@/services/api-types";

type ContentStudioCardProps = {
  content?: MarketingContent;
  businessName?: string;
  isLoading?: boolean;
  error?: string | null;
  isRegenerating?: boolean;
  isApproving?: boolean;
  creative?: CreativeAsset;
  creatives?: CreativeAsset[];
  isCreativeLoading?: boolean;
  isCreativePending?: boolean;
  creativeError?: string | null;
  creativePhase?: CreativePhase | null;
  onRetry?: () => void;
  onGenerate: () => void;
  onRegenerate: (content: MarketingContent) => void;
  onApprove: (content: MarketingContent) => void;
  onSchedule: (content: MarketingContent) => void;
  publishingCapability?: PublishingCapability;
  isPreparingPublish?: boolean;
  onPreparePublish?: (content: MarketingContent) => void;
  onConnectChannel?: () => void;
  onEdit?: (content: MarketingContent) => void;
  onHistory?: (content: MarketingContent) => void;
  onCreateCreative: (mediaType: CreativeMediaType) => void;
  onEditCreativeDirection?: (mediaType: CreativeMediaType) => void;
  onReloadCreative?: () => void;
  onRetryCreative: (creative: CreativeAsset) => void;
  onRegenerateCreative: (creative: CreativeAsset) => void;
  onVariationCreative?: (
    creative: CreativeAsset,
    mode: CreativeVariationMode,
  ) => void;
};

export function CmoContentStudioCard({
  content,
  businessName,
  isLoading = false,
  error,
  isRegenerating = false,
  isApproving = false,
  creative,
  creatives,
  isCreativeLoading = false,
  isCreativePending = false,
  creativeError,
  creativePhase = null,
  onRetry,
  onGenerate,
  onRegenerate,
  onApprove,
  onSchedule,
  publishingCapability,
  isPreparingPublish = false,
  onPreparePublish,
  onConnectChannel,
  onEdit,
  onHistory,
  onCreateCreative,
  onEditCreativeDirection,
  onReloadCreative,
  onRetryCreative,
  onRegenerateCreative,
  onVariationCreative,
}: ContentStudioCardProps) {
  if (isLoading) {
    return (
      <Card>
        <SectionTitle
          title="Content Studio"
          action={<Badge>AI CMO</Badge>}
        />

        <div className="empty" role="status" aria-live="polite">
          <RefreshCw className="spin" />
          <p>Loading your content workspace…</p>
        </div>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <SectionTitle
          title="Content Studio"
          action={<Badge tone="warning">Needs attention</Badge>}
        />

        <div className="empty" role="alert" aria-live="assertive">
          <AlertCircle />
          <h3>Content Studio could not load</h3>
          <p>{error}</p>

          {onRetry && (
            <Button onClick={onRetry}>
              <RefreshCw />
              Retry content
            </Button>
          )}
        </div>
      </Card>
    );
  }

  if (!content) {
    return (
      <Card>
        <SectionTitle
          title="Content Studio"
          action={<Badge tone="info">Business Brain ready</Badge>}
        />

        <div className="empty">
          <h3>Create your first grounded marketing draft</h3>
          <p>
            Generate brand-aware content from the trusted Business Brain.
            Nothing is published until you review and approve it.
          </p>

          <Button variant="primary" className="cmo-card-cta" onClick={onGenerate}>
            <WandSparkles />
            New content
          </Button>
        </div>
      </Card>
    );
  }

  const groundingSummary = content.source_evidence?.some(
    (item) => item.classification === "trusted_context_assembly",
  );
  const publishingEligible = ["approved", "scheduled", "ready_to_publish"].includes(
    content.status,
  );
  const contentActionPending = isRegenerating || isApproving || isPreparingPublish;
  const canPreparePublishing = Boolean(
    publishingCapability?.canPrepare && onPreparePublish,
  );
  const publishingLive = publishingCapability?.state === "checking"
    ? "polite"
    : publishingCapability?.state === "unverified"
      ? "assertive"
      : undefined;
  const nextStep = content.status === "draft"
    ? "Review and approve the draft"
    : content.status === "review"
      ? "Make an approval decision"
      : content.status === "approved"
        ? canPreparePublishing
          ? "Prepare publishing or choose a schedule"
          : "Choose when this content should run"
        : content.status === "scheduled"
          ? canPreparePublishing
            ? "Prepare the governed publishing action when ready"
            : "The content is on your internal calendar"
          : content.status === "ready_to_publish"
            ? "Prepare the governed publishing action"
            : "This content is archived";
  const creativeState = creative ? creativeMediaPresentation(creative) : null;

  return (
    <Card className="cmo-studio-card">
      <SectionTitle
        title="Content Studio"
        action={
          <div className="toolbar">
            {content.ai_generated && (
              <Badge tone="info">
                <Sparkles />
                AI generated
              </Badge>
            )}
            {groundingSummary && (
              <Badge><ShieldCheck /> Grounded</Badge>
            )}
            <ContentStatusBadge status={content.status} />
          </div>
        }
      />

      <section className="cmo-studio-preview" aria-label="Marketing content preview">
        <div className="cmo-studio-preview-head">
          <div>
            <div className="eyebrow">
              {readableContentValue(content.channel)} · {readableContentValue(content.content_type)}
            </div>
            <strong>{businessName?.trim() || "Marketing preview"}</strong>
          </div>
          <Badge>Version {content.version}</Badge>
        </div>

        <div className="cmo-studio-preview-body">
          <div className="eyebrow">
            {content.ai_generated ? "AI CMO draft" : "Manual draft"}
          </div>
          <h2>{content.title}</h2>
          <p>{content.body}</p>
          {content.cta && (
            <div className="cmo-studio-cta">
              <span>Call to action</span>
              <strong>{content.cta}</strong>
            </div>
          )}
        </div>
      </section>

      <section className="cmo-studio-creative" aria-label="Visual creative">
        <SectionTitle
          title="Visual creative"
          action={
            creativeError ? (
              <Badge tone="warning">Needs attention</Badge>
            ) : isCreativeLoading ? (
              <Badge tone="info">Loading creative</Badge>
            ) : isCreativePending ? (
              <Badge tone="info">Creating revision</Badge>
            ) : creativeState ? (
              <Badge tone={creativeState.tone}>{creativeState.label}</Badge>
            ) : (
              <Badge>Optional</Badge>
            )
          }
        />
        <CmoCreativePanel
          creative={creative}
          creatives={creatives}
          isLoading={isCreativeLoading}
          error={creativeError}
          phase={creativePhase}
          contentId={content.id}
          channel={content.channel}
          contentType={content.content_type}
          isPending={isCreativePending}
          onCreate={onCreateCreative}
          onEditDirection={onEditCreativeDirection}
          onReload={onReloadCreative}
          onRetry={onRetryCreative}
          onRegenerate={onRegenerateCreative}
          onVariation={onVariationCreative}
        />
      </section>

      <AIDetailsDisclosure content={content} />

      <div className="cmo-studio-action-region">
        <div className="cmo-studio-next-step">
          <span>Next step</span>
          <strong>{nextStep}</strong>
        </div>
        <div className="cmo-studio-actions">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="secondary" className="btn-sm" disabled={contentActionPending} aria-label="More content actions">
                <MoreHorizontal /> More
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="cmo-content-menu">
              {onEdit && (
                <DropdownMenuItem onSelect={() => onEdit(content)}><Pencil /> Edit content</DropdownMenuItem>
              )}
              <DropdownMenuItem onSelect={() => onRegenerate(content)} data-testid="button-regenerate-content">
                <RefreshCw /> {isRegenerating ? "Regenerating…" : "Regenerate copy"}
              </DropdownMenuItem>
              {onHistory && (
                <DropdownMenuItem onSelect={() => onHistory(content)}><FileClock /> Version history</DropdownMenuItem>
              )}
              <DropdownMenuItem onSelect={onGenerate}><Plus /> Create another</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <div className="cmo-studio-primary-actions">
            {["draft", "review"].includes(content.status) && (
              <Button
                variant="primary"
                className="btn-sm"
                disabled={contentActionPending}
                onClick={() => onApprove(content)}
                data-testid="button-approve-content"
              >
                <Check />
                {isApproving ? "Approving…" : content.status === "draft" ? "Review & approve" : "Approve content"}
              </Button>
            )}

            {content.status === "approved" && (
              <Button
                variant={canPreparePublishing ? "secondary" : "primary"}
                className="btn-sm"
                disabled={contentActionPending}
                onClick={() => onSchedule(content)}
                data-testid="button-schedule-content"
              >
                <Calendar /> Schedule
              </Button>
            )}
            {publishingEligible && publishingCapability?.canPrepare && onPreparePublish && (
              <Button
                variant="primary"
                className="btn-sm"
                disabled={contentActionPending}
                onClick={() => onPreparePublish(content)}
                data-testid="button-prepare-publish"
              >
                <Send /> {isPreparingPublish ? "Preparing…" : "Prepare publish"}
              </Button>
            )}
          </div>
        </div>
      </div>

      {publishingEligible && publishingCapability && !publishingCapability.canPrepare && (
        <div
          className="cmo-publishing-readiness"
          aria-live={publishingLive}
          role={publishingLive === "polite" ? "status" : publishingLive === "assertive" ? "alert" : undefined}
        >
          <Send />
          <span>{publishingCapability.copy}</span>
          {onConnectChannel && publishingCapability.state === "unavailable" && (
            <Button variant="tertiary" className="btn-sm" onClick={onConnectChannel}>
              Connect channel
            </Button>
          )}
        </div>
      )}
    </Card>
  );
}
