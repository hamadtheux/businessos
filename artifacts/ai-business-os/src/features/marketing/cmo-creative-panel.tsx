import {
  useEffect,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import {
  AlertCircle,
  ExternalLink,
  Image as ImageIcon,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import { Badge, Button } from "@/components/product-ui";
import {
  safeCreativeMediaUrl,
  type CreativePhase as CreativePhaseValue,
} from "@/lib/cmo-ux";
import type { CreativeAsset } from "@/services/api-types";

export type CreativePhase = CreativePhaseValue;

type CmoCreativePanelProps = {
  creative?: CreativeAsset;
  creatives?: CreativeAsset[];
  isLoading?: boolean;
  error?: string | null;
  phase?: CreativePhase | null;
  onCreate: () => void;
  onReload?: () => void;
  onRetry: (creative: CreativeAsset) => void;
  onRegenerate: (creative: CreativeAsset) => void;
};

const panelStyle: CSSProperties = {
  marginTop: 16,
  overflow: "hidden",
  border: "1px solid var(--border, #e4e7ec)",
  borderRadius: 18,
  background: "var(--surface, #ffffff)",
};

const stateStyle: CSSProperties = {
  display: "flex",
  minHeight: 180,
  alignItems: "center",
  justifyContent: "center",
  padding: 24,
  textAlign: "center",
};

const historyStyle: CSSProperties = {
  padding: 14,
  borderTop: "1px solid var(--border, #e4e7ec)",
  background: "var(--surface-subtle, #f8fafc)",
};

function WorkingState({ phase }: { phase: CreativePhase }) {
  return (
    <div style={stateStyle} data-testid={`creative-loading-${phase}`} aria-live="polite">
      <div className="empty" style={{ minHeight: 0 }}>
        <RefreshCw className="spin" />
        <h3>{phase === "strategy" ? "Creating the visual strategy" : "Designing your branded creative"}</h3>
        <p>
          {phase === "strategy"
            ? "9D Brain is grounding the campaign angle, copy, and art direction in your business context."
            : "9D Brain is combining your campaign direction, brand identity, copy, and visual into the final creative."}
        </p>
      </div>
    </div>
  );
}

function StateFrame({ testId, children }: { testId: string; children: ReactNode }) {
  return (
    <div style={stateStyle} data-testid={testId}>
      <div className="empty" style={{ minHeight: 0 }}>
        {children}
      </div>
    </div>
  );
}

export function CmoCreativePanel({
  creative,
  creatives,
  isLoading = false,
  error,
  phase = null,
  onCreate,
  onReload,
  onRetry,
  onRegenerate,
}: CmoCreativePanelProps) {
  const availableCreatives = creatives?.length
    ? creatives
    : creative
      ? [creative]
      : [];
  const featured = creative ?? availableCreatives[0];
  const [historyId, setHistoryId] = useState<string | null>(null);
  const [failedPreviewId, setFailedPreviewId] = useState<string | null>(null);
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const displayed = availableCreatives.find((item) => item.id === historyId) ?? featured;
  const previousCreatives = availableCreatives.filter(
    (item) => item.id !== featured?.id,
  );
  const isHistorical = Boolean(displayed && displayed.id !== featured?.id);
  const safeReference = safeCreativeMediaUrl(displayed?.storage_reference);
  const previewFailed = Boolean(displayed && failedPreviewId === displayed.id);

  useEffect(() => {
    setHistoryId(null);
  }, [featured?.id]);

  useEffect(() => {
    setFailedPreviewId(null);
    setPreviewAttempt(0);
  }, [displayed?.id, displayed?.storage_reference]);

  if (phase) {
    return <div style={panelStyle}><WorkingState phase={phase} /></div>;
  }

  if (isLoading) {
    return (
      <div style={panelStyle}>
        <div style={stateStyle} data-testid="creative-loading-assets" aria-live="polite">
          <RefreshCw className="spin" />
          <span style={{ marginLeft: 10 }}>Loading creative history…</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div style={panelStyle}>
        <StateFrame testId="creative-error">
          <AlertCircle />
          <h3>Creative history could not load</h3>
          <p>{error}</p>
          {onReload && (
            <Button onClick={onReload} data-testid="button-reload-creatives">
              <RefreshCw /> Retry history
            </Button>
          )}
        </StateFrame>
      </div>
    );
  }

  if (!displayed) {
    return (
      <div style={panelStyle}>
        <StateFrame testId="creative-empty-state">
          <ImageIcon />
          <h3>No visual creative yet</h3>
          <p>Turn this grounded draft into a professionally composed, branded image.</p>
          <Button variant="primary" onClick={onCreate} data-testid="button-create-visual">
            <Sparkles /> Create visual
          </Button>
        </StateFrame>
      </div>
    );
  }

  let currentState: ReactNode;

  if (displayed.generation_status === "provider_required") {
    currentState = (
      <StateFrame testId="creative-provider-required">
        <AlertCircle />
        <h3>Image generation is temporarily unavailable</h3>
        <p>Your creative strategy is saved. Try again shortly—nothing has been lost.</p>
        {!isHistorical && (
          <Button onClick={() => onRetry(displayed)}>
            <RefreshCw /> Try again
          </Button>
        )}
      </StateFrame>
    );
  } else if (displayed.generation_status === "failed") {
    currentState = (
      <StateFrame testId="creative-failed-state">
        <AlertCircle />
        <h3>The visual could not be completed</h3>
        <p>No unfinished image was attached. Your grounded strategy is safe and ready to retry.</p>
        {!isHistorical && (
          <Button onClick={() => onRetry(displayed)} data-testid="button-retry-creative">
            <RefreshCw /> Retry visual
          </Button>
        )}
      </StateFrame>
    );
  } else if (displayed.generation_status === "brief_ready") {
    currentState = (
      <StateFrame testId="creative-brief-ready">
        <ShieldCheck />
        <h3>Creative strategy ready</h3>
        <p>The campaign angle and visual direction are grounded. Generate the final branded image when you are ready.</p>
        {!isHistorical && (
          <Button variant="primary" onClick={() => onRetry(displayed)}>
            <Sparkles /> Generate final visual
          </Button>
        )}
      </StateFrame>
    );
  } else if (displayed.generation_status === "ready" && safeReference) {
    currentState = (
      <div data-testid="creative-ready-preview">
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            padding: "13px 16px",
            borderBottom: "1px solid var(--border, #e4e7ec)",
          }}
        >
          <div>
            <div className="eyebrow">
              {isHistorical ? "Previous final creative" : "Final branded creative"}
            </div>
            <strong>{displayed.width} × {displayed.height} PNG</strong>
          </div>
          <Badge tone="success"><ShieldCheck /> Ready for review</Badge>
        </div>

        {previewFailed ? (
          <div data-testid="creative-preview-unavailable">
            <StateFrame testId="creative-broken-preview">
              <ImageIcon />
              <h3>Creative is ready, but the preview could not be loaded.</h3>
              <p>This is a temporary preview issue. The creative status has not changed.</p>
              <div className="empty-actions">
                <Button
                  onClick={() => {
                    setFailedPreviewId(null);
                    setPreviewAttempt((attempt) => attempt + 1);
                  }}
                  data-testid="button-retry-preview"
                >
                  <RefreshCw /> Retry preview
                </Button>
                <a
                  className="btn btn-secondary btn-sm"
                  href={safeReference}
                  target="_blank"
                  rel="noreferrer"
                >
                  <ExternalLink /> Preview full size
                </a>
              </div>
            </StateFrame>
          </div>
        ) : (
          <div style={{ background: "var(--surface-subtle, #f2f4f7)", padding: 14 }}>
            <img
              key={`${displayed.id}-${previewAttempt}`}
              src={safeReference}
              alt={displayed.alt_text || "Branded marketing creative preview"}
              onError={() => setFailedPreviewId(displayed.id)}
              style={{
                display: "block",
                width: "100%",
                maxHeight: 560,
                objectFit: "contain",
                borderRadius: 12,
                background: "var(--surface, #ffffff)",
              }}
            />
          </div>
        )}

        {!previewFailed && (
          <div className="toolbar" style={{ justifyContent: "space-between", padding: 14, flexWrap: "wrap" }}>
            <a
              className="btn btn-secondary btn-sm"
              href={safeReference}
              target="_blank"
              rel="noreferrer"
            >
              <ExternalLink /> Preview full size
            </a>
            {!isHistorical && (
              <Button
                className="btn-sm"
                onClick={() => onRegenerate(displayed)}
                data-testid="button-regenerate-creative"
              >
                <RefreshCw /> Regenerate as new creative
              </Button>
            )}
          </div>
        )}
      </div>
    );
  } else {
    currentState = (
      <StateFrame testId="creative-unavailable-state">
        <ImageIcon />
        <h3>Visual unavailable</h3>
        <p>This record is preserved in history but does not have an available final preview.</p>
      </StateFrame>
    );
  }

  return (
    <div style={panelStyle}>
      {currentState}

      {previousCreatives.length > 0 && (
        <div style={historyStyle} data-testid="creative-history">
          <div
            className="toolbar"
            style={{ justifyContent: "space-between", marginBottom: 10 }}
          >
            <strong style={{ fontSize: 12 }}>
              Creative history · {previousCreatives.length} previous
            </strong>
            {isHistorical && (
              <Button className="btn-sm" onClick={() => setHistoryId(null)}>
                Back to latest
              </Button>
            )}
          </div>
          <div className="toolbar" aria-label="Previous creative artwork">
            {previousCreatives.map((item, index) => (
              <Button
                key={item.id}
                variant="secondary"
                className="btn-sm"
                onClick={() => setHistoryId(item.id)}
                aria-pressed={item.id === displayed.id}
              >
                <ImageIcon /> Previous {index + 1} · {item.generation_status.replaceAll("_", " ")}
              </Button>
            ))}
          </div>
          <p className="subtle" style={{ margin: "10px 0 0", fontSize: 10 }}>
            Previous artwork is preserved in history and remains read-only here.
          </p>
        </div>
      )}
    </div>
  );
}
