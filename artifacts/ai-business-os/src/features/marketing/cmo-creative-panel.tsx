import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AlertCircle,
  ExternalLink,
  FileClock,
  Image as ImageIcon,
  Layers3,
  Pencil,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Video,
} from "lucide-react";

import { Badge, Button } from "@/components/product-ui";
import {
  recommendedCreativeMediaForContent,
  safeCreativeMediaUrl,
  videoFormatForContent,
  type CreativeMediaType,
  type CreativePhase as CreativePhaseValue,
} from "@/lib/cmo-ux";
import type {
  CreativeAsset,
  MarketingChannel,
  MarketingContentType,
} from "@/services/api-types";

export type CreativePhase = CreativePhaseValue;
export type CreativeVariationMode =
  | "alternate_metaphor"
  | "product_led"
  | "outcome_led"
  | "minimal"
  | "cinematic"
  | "alternate_composition";

type CmoCreativePanelProps = {
  creative?: CreativeAsset;
  creatives?: CreativeAsset[];
  isLoading?: boolean;
  error?: string | null;
  actionError?: string | null;
  isPending?: boolean;
  phase?: CreativePhase | null;
  contentId?: string;
  channel?: MarketingChannel;
  contentType?: MarketingContentType;
  onCreate: (mediaType: CreativeMediaType) => void;
  onEditDirection?: (mediaType: CreativeMediaType) => void;
  onReload?: () => void;
  onRetry: (creative: CreativeAsset) => void;
  onRegenerate: (creative: CreativeAsset) => void;
  onVariation?: (creative: CreativeAsset, mode: CreativeVariationMode) => void;
};

function StudioHeader({
  mediaType,
  recommendedMedia,
  onChange,
  disabled,
}: {
  mediaType: CreativeMediaType;
  recommendedMedia: CreativeMediaType;
  onChange: (value: CreativeMediaType) => void;
  disabled: boolean;
}) {
  return (
    <div className="cmo-creative-studio-head">
      <div><div className="eyebrow">Creative Studio</div><strong>Campaign media</strong><span className="cmo-creative-recommendation">{recommendedMedia === "video" ? "Video" : "Image"} recommended</span></div>
      <div className="cmo-media-switch" role="group" aria-label="Creative media type">
        <button type="button" className={mediaType === "image" ? "active" : undefined} aria-pressed={mediaType === "image"} onClick={() => onChange("image")} disabled={disabled}>
          <ImageIcon /> Image
        </button>
        <button type="button" className={mediaType === "video" ? "active" : undefined} aria-pressed={mediaType === "video"} onClick={() => onChange("video")} disabled={disabled}>
          <Video /> Video
        </button>
      </div>
    </div>
  );
}

function StateFrame({
  testId,
  children,
  live,
}: {
  testId: string;
  children: ReactNode;
  live?: "polite" | "assertive";
}) {
  return <div className="cmo-creative-state" data-testid={testId} aria-live={live} role={live === "assertive" ? "alert" : live === "polite" ? "status" : undefined}><div className="empty compact-empty">{children}</div></div>;
}

function WorkingState({ phase }: { phase: CreativePhase }) {
  const isVideo = phase.startsWith("video_");
  const title = phase === "video_strategy"
    ? "Planning video…"
    : phase === "video_review"
      ? "Reviewing video…"
      : isVideo
        ? "Generating video…"
        : phase === "strategy"
          ? "Preparing creative direction…"
          : "Generating branded image…";
  return (
    <StateFrame testId={`creative-loading-${phase}`} live="polite">
      <RefreshCw className="spin" />
      <h3>{title}</h3>
      <p>{isVideo ? "Your strategy is saved while 9D Brain prepares the next stage." : "AI CMO is grounding the campaign story, then composing exact copy and brand identity."}</p>
    </StateFrame>
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
  contentId,
  channel,
  contentType,
  onCreate,
  onEditDirection,
  onReload,
  onRetry,
  onRegenerate,
  onVariation,
}: CmoCreativePanelProps) {
  const allCreatives = creatives?.length ? creatives : creative ? [creative] : [];
  const recommendationContext = {
    channel: channel ?? "other",
    content_type: contentType ?? "social_post",
  } as const;
  const recommendedMedia = recommendedCreativeMediaForContent(recommendationContext);
  const recommendedVideoFormat = videoFormatForContent(recommendationContext);
  const initialMedia = creative?.media_type ?? creatives?.[0]?.media_type ?? recommendedMedia;
  const [mediaType, setMediaType] = useState<CreativeMediaType>(initialMedia);
  const [historyId, setHistoryId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [failedPreviewId, setFailedPreviewId] = useState<string | null>(null);
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const contentScopeId = contentId ?? creative?.content_id ?? creatives?.[0]?.content_id;
  const mediaCreatives = useMemo(
    () => allCreatives.filter((item) => (item.media_type ?? "image") === mediaType),
    [allCreatives, mediaType],
  );
  const featured = creative && (creative.media_type ?? "image") === mediaType ? creative : mediaCreatives[0];
  const displayed = mediaCreatives.find((item) => item.id === historyId) ?? featured;
  const previousCreatives = mediaCreatives.filter((item) => item.id !== featured?.id);
  const isHistorical = Boolean(displayed && displayed.id !== featured?.id);
  const safeReference = safeCreativeMediaUrl(displayed?.storage_reference);
  const previewFailed = Boolean(displayed && failedPreviewId === displayed.id);

  useEffect(() => {
    setHistoryId(null);
    setHistoryOpen(false);
  }, [featured?.id, mediaType]);

  useEffect(() => {
    setMediaType(initialMedia);
  }, [contentScopeId, initialMedia]);

  useEffect(() => {
    setFailedPreviewId(null);
    setPreviewAttempt(0);
  }, [displayed?.id, displayed?.storage_reference]);

  const header = <StudioHeader mediaType={mediaType} recommendedMedia={recommendedMedia} onChange={setMediaType} disabled={isPending || isLoading} />;

  if (phase) return <div className="cmo-creative-panel">{header}<WorkingState phase={phase} /></div>;
  if (isLoading) {
    return <div className="cmo-creative-panel">{header}<StateFrame testId="creative-loading-assets" live="polite"><RefreshCw className="spin" /><p>Loading creative history…</p></StateFrame></div>;
  }
  if (error) {
    return (
      <div className="cmo-creative-panel">{header}<StateFrame testId="creative-error" live="assertive"><AlertCircle /><h3>Creative history could not load</h3><p>{error}</p>{onReload && <Button onClick={onReload} disabled={isPending} data-testid="button-reload-creatives"><RefreshCw /> Retry history</Button>}</StateFrame></div>
    );
  }

  let currentState: ReactNode;
  if (!displayed) {
    currentState = actionError ? (
      <StateFrame testId="creative-operation-error" live="assertive"><AlertCircle /><h3>{mediaType === "video" ? "Video strategy could not be prepared" : "Image could not be completed"}</h3><p>{actionError}</p><Button onClick={() => onCreate(mediaType)} disabled={isPending} data-testid="button-retry-creative-operation"><RefreshCw /> Retry</Button></StateFrame>
    ) : (
      <StateFrame testId={`creative-${mediaType}-empty-state`}>
        {mediaType === "video" ? <Video /> : <ImageIcon />}
        <h3>{mediaType === "video" ? "Create a branded campaign video" : "Create a branded image"}</h3>
        <p>{mediaType === "video" ? `${recommendedVideoFormat.aspect_ratio} · ${recommendedVideoFormat.duration_seconds} seconds for this format. AI CMO prepares the hook, script, storyboard, scenes, continuity, audio, captions, and end card.` : "AI CMO builds a business-specific visual story and composes exact brand copy for this format."}</p>
        <div className="empty-actions">
          <Button variant="primary" onClick={() => onCreate(mediaType)} disabled={isPending} data-testid={`button-create-${mediaType}`}><Sparkles /> Generate {mediaType}</Button>
          {onEditDirection && <Button variant="secondary" onClick={() => onEditDirection(mediaType)} disabled={isPending}><Pencil /> Advanced direction</Button>}
        </div>
        <span className="cmo-creative-assurance">Nothing will be published automatically.</span>
      </StateFrame>
    );
  } else if (actionError && displayed.generation_status !== "ready") {
    currentState = <StateFrame testId="creative-operation-error" live="assertive"><AlertCircle /><h3>Creative could not be completed</h3><p>{actionError}</p>{!isHistorical && <Button onClick={() => onRetry(displayed)} disabled={isPending}><RefreshCw /> Retry</Button>}</StateFrame>;
  } else if (displayed.generation_status === "provider_required") {
    currentState = (
      <StateFrame testId="creative-provider-required">
        <AlertCircle /><h3>{mediaType === "video" ? "Video generation isn’t connected yet" : "Image generation is temporarily unavailable"}</h3>
        <p>{mediaType === "video" ? "Your video strategy and storyboard are saved. Connect a supported video service to render the final video. Nothing has been published." : "Your creative strategy is saved. Try again shortly—nothing has been lost."}</p>
        {!isHistorical && <Button onClick={() => onRetry(displayed)} disabled={isPending}><RefreshCw /> Check again</Button>}
      </StateFrame>
    );
  } else if (["strategy_ready", "brief_ready"].includes(displayed.generation_status)) {
    currentState = (
      <StateFrame testId="creative-brief-ready"><ShieldCheck /><h3>{mediaType === "video" ? "Video strategy ready" : "Creative strategy ready"}</h3><p>{mediaType === "video" ? "The hook, storyboard, timed scenes, continuity, audio, captions, and end card are saved." : "The campaign angle and visual direction are grounded."}</p>{!isHistorical && <Button variant="primary" onClick={() => onRetry(displayed)} disabled={isPending}><Sparkles /> Generate {mediaType}</Button>}</StateFrame>
    );
  } else if (["queued", "generating", "reviewing", "repairing"].includes(displayed.generation_status)) {
    const statusCopy = displayed.generation_status === "queued"
      ? `${mediaType === "video" ? "Video" : "Image"} queued`
      : displayed.generation_status === "generating"
        ? `Generating ${mediaType}`
        : displayed.generation_status === "repairing"
          ? `Refining ${mediaType}`
          : `Reviewing ${mediaType}`;
    currentState = <StateFrame testId="creative-generation-progress" live="polite"><RefreshCw className="spin" /><h3>{statusCopy}</h3><p>Your strategy is saved while 9D Brain prepares the next stage.</p>{onReload && <Button onClick={onReload} disabled={isPending}><RefreshCw /> Refresh status</Button>}</StateFrame>;
  } else if (displayed.generation_status === "failed") {
    currentState = <StateFrame testId="creative-failed-state" live="assertive"><AlertCircle /><h3>The {mediaType} could not be completed</h3><p>No unfinished media was attached. The grounded strategy is preserved.</p>{!isHistorical && <Button onClick={() => onRetry(displayed)} disabled={isPending}><RefreshCw /> Retry {mediaType}</Button>}</StateFrame>;
  } else if (displayed.generation_status === "ready" && safeReference) {
    currentState = (
      <div className="cmo-creative-ready" data-testid="creative-ready-preview">
        <div className="cmo-creative-ready-head"><div><div className="eyebrow">{isHistorical ? "Previous creative" : "Current creative"}</div><strong>{mediaType === "video" ? `${displayed.duration_seconds ?? "—"} second video` : displayed.width && displayed.height ? `${displayed.width} × ${displayed.height} PNG` : "Branded image"}</strong><span>{new Date(displayed.created_at).toLocaleString()}</span></div><Badge tone="success"><ShieldCheck /> Ready for review</Badge></div>
        {previewFailed ? (
          <div data-testid="creative-preview-unavailable"><StateFrame testId="creative-broken-preview" live="assertive">{mediaType === "video" ? <Video /> : <ImageIcon />}<h3>Creative is ready, but the preview could not be loaded.</h3><Button data-testid="button-retry-preview" onClick={() => { setFailedPreviewId(null); setPreviewAttempt((value) => value + 1); }}><RefreshCw /> Retry preview</Button></StateFrame></div>
        ) : (
          <div className="cmo-creative-image-wrap">{mediaType === "video" ? <video key={`${displayed.id}-${previewAttempt}`} src={safeReference} controls preload="metadata" onError={() => setFailedPreviewId(displayed.id)} className="cmo-creative-image" /> : <img key={`${displayed.id}-${previewAttempt}`} src={safeReference} alt={displayed.alt_text || "Branded marketing creative preview"} onError={() => setFailedPreviewId(displayed.id)} className="cmo-creative-image" />}</div>
        )}
        <div className="cmo-creative-actions">
          <a className="btn btn-secondary btn-sm" href={safeReference} target="_blank" rel="noreferrer"><ExternalLink /> Preview</a>
          {!isHistorical && mediaType === "image" && <>{onVariation && <Button className="btn-sm" onClick={() => onVariation(displayed, "alternate_metaphor")} disabled={isPending}><Layers3 /> Variations</Button>}<Button className="btn-sm" onClick={() => onRegenerate(displayed)} disabled={isPending}><RefreshCw /> Regenerate</Button></>}
          {onEditDirection && <Button variant="tertiary" className="btn-sm" onClick={() => onEditDirection(mediaType)} disabled={isPending}><Pencil /> Edit direction</Button>}
        </div>
      </div>
    );
  } else {
    currentState = <StateFrame testId="creative-unavailable-state">{mediaType === "video" ? <Video /> : <ImageIcon />}<h3>Creative unavailable</h3><p>This immutable history record has no final preview.</p></StateFrame>;
  }

  return (
    <div className="cmo-creative-panel">
      {header}
      {actionError && displayed?.generation_status === "ready" && <div className="cmo-creative-inline-error" role="alert" aria-live="assertive"><AlertCircle /><div><strong>New creative could not be completed</strong><span>{actionError}</span></div></div>}
      {currentState}
      {displayed && previousCreatives.length > 0 && <div className="cmo-creative-footer-actions"><Button variant="tertiary" className="btn-sm" onClick={() => setHistoryOpen((value) => !value)} disabled={isPending} aria-expanded={historyOpen} aria-controls={`creative-history-${mediaType}`}><FileClock /> History ({previousCreatives.length})</Button></div>}
      {historyOpen && previousCreatives.length > 0 && (
        <div id={`creative-history-${mediaType}`} className="cmo-creative-history" data-testid="creative-history">
          <div className="cmo-creative-history-head"><strong>Immutable {mediaType} history</strong>{isHistorical && <Button className="btn-sm" onClick={() => setHistoryId(null)} disabled={isPending}>Back to latest</Button>}</div>
          <div className="cmo-creative-history-list" aria-label={`Previous ${mediaType} creative`}>{previousCreatives.map((item, index) => <Button key={item.id} variant="secondary" className="btn-sm" onClick={() => setHistoryId(item.id)} disabled={isPending} aria-pressed={item.id === displayed?.id}>{mediaType === "video" ? <Video /> : <ImageIcon />} Previous {index + 1} · {item.generation_status.replaceAll("_", " ")}</Button>)}</div>
          <p className="subtle">Previous media remains read-only and is never overwritten.</p>
        </div>
      )}
    </div>
  );
}
