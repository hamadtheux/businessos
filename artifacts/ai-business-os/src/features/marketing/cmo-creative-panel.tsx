import {
  useEffect,
  useState,
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
  actionError?: string | null;
  isPending?: boolean;
  phase?: CreativePhase | null;
  onCreate: () => void;
  onReload?: () => void;
  onRetry: (creative: CreativeAsset) => void;
  onRegenerate: (creative: CreativeAsset) => void;
};

function WorkingState({ phase }: { phase: CreativePhase }) {
  return (
    <div className="cmo-creative-state cmo-creative-working" data-testid={`creative-loading-${phase}`} aria-live="polite">
      <div className="empty compact-empty">
        <RefreshCw className="spin" />
        <div className="cmo-creative-progress-steps" aria-hidden="true">
          <span className="complete" />
          <span className={phase === "visual" ? "complete" : "active"} />
          <span className={phase === "visual" ? "active" : undefined} />
        </div>
        <h3>{phase === "strategy" ? "Preparing creative direction…" : "Generating your branded visual…"}</h3>
        <p>
          {phase === "strategy"
            ? "AI CMO is grounding the composition in your post, Business Brain, and brand identity."
            : "Your campaign direction, copy, and brand system are being composed into the final image."}
        </p>
      </div>
    </div>
  );
}

function StateFrame({ testId, children }: { testId: string; children: ReactNode }) {
  return (
    <div className="cmo-creative-state" data-testid={testId}>
      <div className="empty compact-empty">
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
  actionError,
  isPending = false,
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
    return <div className="cmo-creative-panel"><WorkingState phase={phase} /></div>;
  }

  if (isLoading) {
    return (
      <div className="cmo-creative-panel">
        <div className="cmo-creative-state" data-testid="creative-loading-assets" aria-live="polite">
          <RefreshCw className="spin" />
          <span style={{ marginLeft: 10 }}>Loading creative history…</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="cmo-creative-panel">
        <StateFrame testId="creative-error">
          <AlertCircle />
          <h3>Creative history could not load</h3>
          <p>{error}</p>
          {onReload && (
            <Button onClick={onReload} disabled={isPending} data-testid="button-reload-creatives">
              <RefreshCw /> Retry history
            </Button>
          )}
        </StateFrame>
      </div>
    );
  }

  if (!displayed) {
    return (
      <div className="cmo-creative-panel">
        {actionError ? (
          <StateFrame testId="creative-operation-error">
            <AlertCircle />
            <h3>Visual could not be completed</h3>
            <p>{actionError}</p>
            <Button onClick={onCreate} disabled={isPending} data-testid="button-retry-creative-operation">
              <RefreshCw /> Retry
            </Button>
          </StateFrame>
        ) : (
          <StateFrame testId="creative-empty-state">
            <ImageIcon />
            <h3>Create a branded visual for this post</h3>
            <p>AI CMO will use your Business Brain, brand identity, campaign direction, and post context.</p>
            <Button variant="primary" onClick={onCreate} disabled={isPending} data-testid="button-create-visual">
              <Sparkles /> Create visual
            </Button>
            <span className="cmo-creative-assurance">Nothing will be published automatically.</span>
          </StateFrame>
        )}
      </div>
    );
  }

  let currentState: ReactNode;

  if (actionError && displayed.generation_status !== "ready") {
    currentState = (
      <StateFrame testId="creative-operation-error">
        <AlertCircle />
        <h3>Visual could not be completed</h3>
        <p>{actionError}</p>
        {!isHistorical && (
          <Button onClick={() => onRetry(displayed)} disabled={isPending} data-testid="button-retry-creative-operation">
            <RefreshCw /> Retry
          </Button>
        )}
      </StateFrame>
    );
  } else if (displayed.generation_status === "provider_required") {
    currentState = (
      <StateFrame testId="creative-provider-required">
        <AlertCircle />
        <h3>Image generation is temporarily unavailable</h3>
        <p>Your creative strategy is saved. Try again shortly—nothing has been lost.</p>
        {!isHistorical && (
          <Button onClick={() => onRetry(displayed)} disabled={isPending}>
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
          <Button onClick={() => onRetry(displayed)} disabled={isPending} data-testid="button-retry-creative">
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
          <Button variant="primary" onClick={() => onRetry(displayed)} disabled={isPending}>
            <Sparkles /> Generate final visual
          </Button>
        )}
      </StateFrame>
    );
  } else if (displayed.generation_status === "ready" && safeReference) {
    currentState = (
      <div className="cmo-creative-ready" data-testid="creative-ready-preview">
        <div className="cmo-creative-ready-head">
          <div>
            <div className="eyebrow">
              {isHistorical ? "Previous creative" : "Current creative"}
            </div>
            <strong>
              {displayed.width && displayed.height
                ? `${displayed.width} × ${displayed.height} PNG`
                : "Branded post visual"}
            </strong>
            <span>{new Date(displayed.created_at).toLocaleString()}</span>
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
          <div className="cmo-creative-image-wrap">
            <img
              key={`${displayed.id}-${previewAttempt}`}
              src={safeReference}
              alt={displayed.alt_text || "Branded marketing creative preview"}
              onError={() => setFailedPreviewId(displayed.id)}
              className="cmo-creative-image"
            />
          </div>
        )}

        {!previewFailed && (
          <div className="cmo-creative-actions">
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
                disabled={isPending}
                data-testid="button-regenerate-creative"
              >
                <RefreshCw /> Regenerate visual
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
    <div className="cmo-creative-panel">
      {actionError && displayed.generation_status === "ready" && (
        <div className="cmo-creative-inline-error" data-testid="creative-operation-error">
          <AlertCircle />
          <div>
            <strong>Visual could not be completed</strong>
            <span>{actionError}</span>
          </div>
          {!isHistorical && (
            <Button
              className="btn-sm"
              onClick={() => onRegenerate(displayed)}
              disabled={isPending}
              data-testid="button-retry-creative-operation"
            >
              Retry
            </Button>
          )}
        </div>
      )}
      {currentState}

      {previousCreatives.length > 0 && (
        <div className="cmo-creative-history" data-testid="creative-history">
          <div className="cmo-creative-history-head">
            <strong>
              Creative history · {previousCreatives.length} previous
            </strong>
            {isHistorical && (
              <Button className="btn-sm" onClick={() => setHistoryId(null)} disabled={isPending}>
                Back to latest
              </Button>
            )}
          </div>
          <div className="cmo-creative-history-list" aria-label="Previous creative artwork">
            {previousCreatives.map((item, index) => (
              <Button
                key={item.id}
                variant="secondary"
                className="btn-sm"
                onClick={() => setHistoryId(item.id)}
                disabled={isPending}
                aria-pressed={item.id === displayed.id}
              >
                <ImageIcon /> Previous {index + 1} · {item.generation_status.replaceAll("_", " ")}
              </Button>
            ))}
          </div>
          <p className="subtle">
            Previous artwork is preserved in history and remains read-only here.
          </p>
        </div>
      )}
    </div>
  );
}
