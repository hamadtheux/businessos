import type { CSSProperties } from "react";
import {
  AlertCircle,
  Calendar,
  Check,
  FileClock,
  Pencil,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Wand2,
  WandSparkles,
} from "lucide-react";

import {
  Badge,
  Button,
  Card,
  SectionTitle,
} from "@/components/product-ui";
import {
  CmoCreativePanel,
  type CreativePhase,
} from "@/features/marketing/cmo-creative-panel";
import type {
  CreativeAsset,
  MarketingContent,
  MarketingContentStatus,
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
  creativeError?: string | null;
  creativePhase?: CreativePhase | null;
  onRetry?: () => void;
  onGenerate: () => void;
  onRegenerate: (content: MarketingContent) => void;
  onApprove: (content: MarketingContent) => void;
  onSchedule: (content: MarketingContent) => void;
  onEdit?: (content: MarketingContent) => void;
  onHistory?: (content: MarketingContent) => void;
  onCreateCreative: () => void;
  onReloadCreative?: () => void;
  onRetryCreative: (creative: CreativeAsset) => void;
  onRegenerateCreative: (creative: CreativeAsset) => void;
};

const studioGridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
  gap: 16,
  marginTop: 14,
  alignItems: "stretch",
};

const previewStyle: CSSProperties = {
  display: "flex",
  minHeight: 360,
  flexDirection: "column",
  overflow: "hidden",
  border: "1px solid var(--border, #e4e7ec)",
  borderRadius: 18,
  background: "var(--surface, #ffffff)",
};

const previewHeaderStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  padding: "14px 16px",
  borderBottom: "1px solid var(--border, #e4e7ec)",
};

const previewBodyStyle: CSSProperties = {
  display: "flex",
  flex: 1,
  flexDirection: "column",
  padding: 20,
};

const previewCopyStyle: CSSProperties = {
  margin: "14px 0 0",
  color: "var(--text, #101828)",
  fontSize: 15,
  lineHeight: 1.7,
  whiteSpace: "pre-wrap",
  overflowWrap: "anywhere",
};

const ctaStyle: CSSProperties = {
  alignSelf: "flex-start",
  marginTop: 18,
  padding: "8px 12px",
  borderRadius: 999,
  background: "var(--surface-subtle, #f2f4f7)",
  color: "var(--text, #101828)",
  fontSize: 12,
  fontWeight: 700,
};

const detailPanelStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 14,
  minHeight: 360,
  padding: 18,
  border: "1px solid var(--border, #e4e7ec)",
  borderRadius: 18,
  background: "var(--surface-subtle, #f8fafc)",
};

const detailBlockStyle: CSSProperties = {
  paddingBottom: 14,
  borderBottom: "1px solid var(--border, #e4e7ec)",
};

const detailTitleStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 7,
  marginBottom: 6,
  color: "var(--muted, #667085)",
  fontSize: 11,
  fontWeight: 800,
  letterSpacing: ".06em",
  textTransform: "uppercase",
};

const detailCopyStyle: CSSProperties = {
  margin: 0,
  color: "var(--text, #101828)",
  fontSize: 13,
  lineHeight: 1.6,
  whiteSpace: "pre-wrap",
};

function readable(value: string) {
  return value.replaceAll("_", " ");
}

function statusTone(
  status: MarketingContentStatus,
): "success" | "warning" | "info" | "neutral" {
  if (
    status === "approved" ||
    status === "scheduled" ||
    status === "ready_to_publish"
  ) {
    return "success";
  }

  if (status === "review") {
    return "info";
  }

  if (status === "draft") {
    return "warning";
  }

  return "neutral";
}

function evidenceSummary(content: MarketingContent) {
  const evidence = content.source_evidence?.find(
    (item) => item.classification === "trusted_context_assembly",
  );

  if (!evidence) {
    return null;
  }

  const summary = evidence.summary;

  return typeof summary === "string" && summary.trim()
    ? summary.trim()
    : null;
}

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
  creativeError,
  creativePhase = null,
  onRetry,
  onGenerate,
  onRegenerate,
  onApprove,
  onSchedule,
  onEdit,
  onHistory,
  onCreateCreative,
  onReloadCreative,
  onRetryCreative,
  onRegenerateCreative,
}: ContentStudioCardProps) {
  if (isLoading) {
    return (
      <Card>
        <SectionTitle
          title="Content Studio"
          action={<Badge>AI CMO</Badge>}
        />

        <div className="empty">
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

        <div className="empty">
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
            Generate content
          </Button>
        </div>
      </Card>
    );
  }

  const groundingSummary = evidenceSummary(content);
  const sourceCount = content.source_evidence?.length ?? 0;

  return (
    <Card>
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

            <Badge tone={statusTone(content.status)}>
              {readable(content.status)}
            </Badge>
          </div>
        }
      />

      <div style={studioGridStyle}>
        <section style={previewStyle} aria-label="Marketing content preview">
          <div style={previewHeaderStyle}>
            <div>
              <div className="eyebrow">
                {readable(content.channel)} ·{" "}
                {readable(content.content_type)}
              </div>

              <strong>
                {businessName?.trim() || "Marketing preview"}
              </strong>
            </div>

            <Badge>Version {content.version}</Badge>
          </div>

          <div style={previewBodyStyle}>
            <div className="eyebrow">
              {content.ai_generated ? "AI CMO draft" : "Manual draft"}
            </div>

            <h2
              style={{
                margin: "8px 0 0",
                fontSize: 22,
                lineHeight: 1.3,
              }}
            >
              {content.title}
            </h2>

            <p style={previewCopyStyle}>{content.body}</p>

            {content.cta && (
              <div style={ctaStyle}>
                CTA · {content.cta}
              </div>
            )}
          </div>
        </section>

        <aside style={detailPanelStyle} aria-label="Content grounding details">
          <div style={detailBlockStyle}>
            <div style={detailTitleStyle}>
              <ShieldCheck size={15} />
              Grounding
            </div>

            <p style={detailCopyStyle}>
              {groundingSummary ??
                (content.ai_generated
                  ? "Generated through the tenant-scoped AI CMO runtime."
                  : "This is a manually authored content version.")}
            </p>

            {sourceCount > 0 && (
              <div
                className="subtle"
                style={{ marginTop: 8, fontSize: 11 }}
              >
                {sourceCount} provenance record
                {sourceCount === 1 ? "" : "s"}
              </div>
            )}
          </div>

          {content.recommended_for && (
            <div style={detailBlockStyle}>
              <div style={detailTitleStyle}>
                <Sparkles size={15} />
                Recommended for
              </div>

              <p style={detailCopyStyle}>
                {content.recommended_for}
              </p>
            </div>
          )}

          {content.creative_brief && (
            <div style={detailBlockStyle}>
              <div style={detailTitleStyle}>
                <Wand2 size={15} />
                Creative direction
              </div>

              <p style={detailCopyStyle}>
                {content.creative_brief}
              </p>
            </div>
          )}

          {content.generation_reasoning && (
            <div>
              <div style={detailTitleStyle}>
                <ShieldCheck size={15} />
                Why this direction
              </div>

              <p style={detailCopyStyle}>
                {content.generation_reasoning}
              </p>
            </div>
          )}
        </aside>
      </div>

      <div style={{ marginTop: 24 }}>
        <SectionTitle
          title="Visual creative"
          action={
            creative?.generation_status === "ready" ? (
              <Badge tone="success">Final artwork</Badge>
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
          onCreate={onCreateCreative}
          onReload={onReloadCreative}
          onRetry={onRetryCreative}
          onRegenerate={onRegenerateCreative}
        />
      </div>

      <div
        className="toolbar"
        style={{
          marginTop: 16,
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <div className="toolbar">
          {onEdit && (
            <Button
              variant="secondary"
              className="btn-sm"
              onClick={() => onEdit(content)}
            >
              <Pencil />
              Edit
            </Button>
          )}

          <Button
            variant="secondary"
            className="btn-sm"
            disabled={isRegenerating}
            onClick={() => onRegenerate(content)}
            data-testid="button-regenerate-content"
          >
            <RefreshCw className={isRegenerating ? "spin" : undefined} />
            {isRegenerating ? "Regenerating…" : "Regenerate"}
          </Button>

          {onHistory && (
            <Button
              variant="secondary"
              className="btn-sm"
              onClick={() => onHistory(content)}
            >
              <FileClock />
              History
            </Button>
          )}
        </div>

        <div className="toolbar">
          {["draft", "review"].includes(content.status) && (
            <Button
              variant="soft"
              className="btn-sm"
              disabled={isApproving}
              onClick={() => onApprove(content)}
              data-testid="button-approve-content"
            >
              <Check />
              {isApproving ? "Approving…" : "Approve"}
            </Button>
          )}

          {content.status === "approved" && (
            <Button
              variant="primary"
              className="btn-sm"
              onClick={() => onSchedule(content)}
              data-testid="button-schedule-content"
            >
              <Calendar />
              Schedule
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}
