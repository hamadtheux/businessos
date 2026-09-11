import {
  AlertTriangle,
  CalendarClock,
  Check,
  ChevronDown,
  ImagePlus,
  LoaderCircle,
  Send,
  Sparkles,
  Upload,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/product-ui";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  PLATFORM_LABELS,
  contentPlatform,
  editableFields,
  type EditablePlatformFields,
  type PlatformReadiness,
} from "@/features/marketing/create-publish-model";
import type { CreativeAsset, MarketingContent } from "@/services/api-types";
import type { CreatePublishPlatform } from "@/services/marketing";

export type PublishProgress = Record<
  CreatePublishPlatform,
  "idle" | "approving" | "publishing" | "published" | "queued" | "connection_required" | "coming_soon" | "failed"
>;

type EditorProps = {
  contents: MarketingContent[];
  activePlatform: CreatePublishPlatform;
  readiness: Record<CreatePublishPlatform, PlatformReadiness>;
  media: CreativeAsset | null;
  mediaHistory: CreativeAsset[];
  mediaLoading: boolean;
  mediaPreviewUrl?: string | null;
  draftState: "saved" | "saving" | "error";
  publishProgress: PublishProgress;
  actionPending: boolean;
  campaignPending: boolean;
  campaignAvailable: boolean;
  canApproveExternal: boolean;
  onPlatformChange: (platform: CreatePublishPlatform) => void;
  onSave: (content: MarketingContent, fields: EditablePlatformFields) => void;
  onRewrite: (content: MarketingContent, instruction: string) => void;
  onUseMedia: (media: CreativeAsset | null) => void;
  onUpload: (file: File, durationSeconds?: number) => Promise<void>;
  onConnect: () => void;
  onSchedule: () => void;
  onPublish: () => void;
  onPublishAndCampaign: () => void;
};

export function CreatePublishEditor({
  contents,
  activePlatform,
  readiness,
  media,
  mediaHistory,
  mediaLoading,
  mediaPreviewUrl,
  draftState,
  publishProgress,
  actionPending,
  campaignPending,
  campaignAvailable,
  canApproveExternal,
  onPlatformChange,
  onSave,
  onRewrite,
  onUseMedia,
  onUpload,
  onConnect,
  onSchedule,
  onPublish,
  onPublishAndCampaign,
}: EditorProps) {
  const content =
    contents.find((item) => contentPlatform(item) === activePlatform) || contents[0];
  const [fields, setFields] = useState<EditablePlatformFields>(() => editableFields(content));
  const [dirty, setDirty] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setFields(editableFields(content));
    setDirty(false);
  }, [content.id]);

  const change = (key: keyof EditablePlatformFields, value: string) => {
    setFields((current) => ({ ...current, [key]: value }));
    setDirty(true);
  };
  const save = () => {
    if (dirty && fields.title.trim() && fields.body.trim()) {
      onSave(content, fields);
      setDirty(false);
    }
  };
  const platforms = contents.map(contentPlatform);
  const publishableCount = platforms.filter(
    (platform) => readiness[platform].canPublish,
  ).length;

  return (
    <section className="create-publish-review" aria-label="Review post">
      <div className="create-publish-review-head">
        <div>
          <p className="eyebrow">Review</p>
          <h2>Your post is ready to shape</h2>
        </div>
        <span className="create-publish-draft-state" data-state={draftState}>
          {draftState === "saving" ? <LoaderCircle /> : draftState === "error" ? <AlertTriangle /> : <Check />}
          {draftState === "saving" ? "Saving…" : draftState === "error" ? "Save paused" : "Draft saved"}
        </span>
      </div>

      <div className="create-publish-review-tabs" role="tablist" aria-label="Platform variants">
        {platforms.map((platform) => {
          const state = readiness[platform];
          return (
            <button
              key={platform}
              type="button"
              role="tab"
              aria-selected={activePlatform === platform}
              data-active={activePlatform === platform}
              onClick={() => onPlatformChange(platform)}
            >
              <span>{PLATFORM_LABELS[platform]}</span>
              <small data-state={state.state}>{state.state === "connected" ? <Check /> : state.label}</small>
            </button>
          );
        })}
      </div>

      <div className="create-publish-workspace">
        <div className="create-publish-media-column">
          <MediaFrame
            media={media}
            fallbackMedia={mediaHistory.find(
              (asset) =>
                asset.id !== media?.id &&
                asset.source_type === "import" &&
                asset.generation_status === "ready" &&
                Boolean(asset.storage_reference),
            ) || null}
            loading={mediaLoading}
            previewUrl={mediaPreviewUrl}
            onUseMedia={onUseMedia}
            onChooseUpload={() => fileRef.current?.click()}
          />
          <input
            ref={fileRef}
            hidden
            type="file"
            accept=".jpg,.jpeg,.png,.webp,.mp4,.webm,image/jpeg,image/png,image/webp,video/mp4,video/webm"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void uploadWithDuration(file, onUpload);
              event.target.value = "";
            }}
          />
          {media?.generation_status === "ready" && (
            <div className="create-publish-media-tools">
              <Button
                variant="tertiary"
                className="btn-sm"
                onClick={() => fileRef.current?.click()}
              >
                <Upload /> Replace media
              </Button>
            </div>
          )}
          {mediaHistory.length > 1 && (
            <details className="create-publish-creative-history">
              <summary>Previous creative history <span>{mediaHistory.length - 1}</span></summary>
              <div>
                {mediaHistory.slice(1).map((asset) => (
                  <article key={asset.id}>
                    {asset.generation_status === "ready" && asset.storage_reference ? (
                      asset.media_type === "video"
                        ? <video src={asset.storage_reference} preload="metadata" />
                        : <img src={asset.storage_reference} alt="Previous creative" />
                    ) : <ImagePlus />}
                    <span>{asset.media_type === "video" ? "Video" : "Image"} · {creativeHistoryStatus(asset.generation_status)}</span>
                  </article>
                ))}
              </div>
            </details>
          )}
        </div>

        <div className="create-publish-editor-panel" role="tabpanel">
          <div className="create-publish-editor-head">
            <div>
              <span>{PLATFORM_LABELS[activePlatform]} variant</span>
              <p>Optimized for the selected platform. Edit anything before publishing.</p>
            </div>
            {content.ai_generated && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="secondary" className="btn-sm">
                    <Sparkles /> AI Assist <ChevronDown />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onSelect={() => onRewrite(content, "Shorten this copy while preserving every supported fact.")}>Shorten</DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => onRewrite(content, "Make this copy more professional without changing its factual meaning.")}>More professional</DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => onRewrite(content, "Make this copy more engaging without adding claims.")}>More engaging</DropdownMenuItem>
                  <DropdownMenuItem onSelect={() => onRewrite(content, `Rewrite this specifically for ${PLATFORM_LABELS[activePlatform]}.`)}>Rewrite for {PLATFORM_LABELS[activePlatform]}</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </div>

          <label className="create-publish-field">
            <span>{activePlatform === "youtube" ? "Title" : "Headline / title"}</span>
            <input
              value={fields.title}
              maxLength={180}
              onChange={(event) => change("title", event.target.value)}
              onBlur={save}
            />
          </label>
          <label className="create-publish-field">
            <span>{activePlatform === "youtube" ? "Description" : activePlatform === "linkedin" ? "Post copy" : "Caption"}</span>
            <textarea
              className="create-publish-editor-copy"
              value={fields.body}
              maxLength={activePlatform === "youtube" ? 8000 : 5000}
              onChange={(event) => change("body", event.target.value)}
              onBlur={save}
            />
          </label>
          <label className="create-publish-field">
            <span>CTA <small>Optional</small></span>
            <input
              value={fields.cta}
              maxLength={300}
              placeholder="Run smarter"
              onChange={(event) => change("cta", event.target.value)}
              onBlur={save}
            />
          </label>
          {activePlatform !== "youtube" && (
            <label className="create-publish-field">
              <span>Hashtags <small>Optional</small></span>
              <input
                value={fields.hashtags}
                placeholder="#smallbusiness #growth"
                onChange={(event) => change("hashtags", event.target.value)}
                onBlur={save}
              />
            </label>
          )}
          {activePlatform === "youtube" && (
            <label className="create-publish-field">
              <span>Keywords <small>Optional</small></span>
              <input
                value={fields.keywords}
                placeholder="operations, small business, automation"
                onChange={(event) => change("keywords", event.target.value)}
                onBlur={save}
              />
            </label>
          )}
          <label className="create-publish-field">
            <span>Alt text <small>Optional</small></span>
            <textarea
              className="create-publish-alt-text"
              value={fields.altText}
              maxLength={1000}
              placeholder="Describe the media for people using screen readers"
              onChange={(event) => change("altText", event.target.value)}
              onBlur={save}
            />
          </label>
        </div>
      </div>

      <PublishResults platforms={platforms} progress={publishProgress} />

      <div className="create-publish-action-bar">
        <div>
          <strong>{platforms.length} platform{platforms.length === 1 ? "" : "s"}</strong>
          <span>{!canApproveExternal && publishableCount
            ? "Owner or admin approval is required to publish"
            : publishableCount
              ? `${publishableCount} connected · Approval is required before any external write`
              : "Connect a supported platform to publish now"}</span>
        </div>
        {!publishableCount && platforms.some((platform) => readiness[platform].state === "disconnected") && (
          <Button variant="tertiary" onClick={onConnect}>Manage connections</Button>
        )}
        <Button
          variant="secondary"
          onClick={onSchedule}
          disabled={
            actionPending ||
            dirty ||
            draftState !== "saved"
          }
          data-testid="schedule-post"
        >
          <CalendarClock /> Schedule
        </Button>

        <Button
          variant="secondary"
          onClick={onPublish}
          disabled={
            actionPending ||
            dirty ||
            draftState !== "saved" ||
            !publishableCount ||
            !canApproveExternal
          }
          data-testid="publish-only"
        >
          <Send /> Publish only
        </Button>

        <Button
          variant="primary"
          onClick={onPublishAndCampaign}
          disabled={
            actionPending ||
            dirty ||
            draftState !== "saved" ||
            !publishableCount ||
            !canApproveExternal ||
            !campaignAvailable
          }
          title={
            campaignAvailable
              ? "Publish organically, then prepare a paid campaign for your review."
              : "Upload and select media before preparing a paid campaign."
          }
          data-testid="publish-run-campaign"
        >
          <Sparkles />{" "}
          {campaignPending
            ? "Publishing & preparing…"
            : "Publish + Run Campaign"}
        </Button>
      </div>
    </section>
  );
}

function MediaFrame({
  media,
  fallbackMedia,
  loading,
  previewUrl,
  onUseMedia,
  onChooseUpload,
}: {
  media: CreativeAsset | null;
  fallbackMedia: CreativeAsset | null;
  loading: boolean;
  previewUrl?: string | null;
  onUseMedia: (media: CreativeAsset | null) => void;
  onChooseUpload: () => void;
}) {
  const source = media?.storage_reference || previewUrl;

  if (loading) {
    return (
      <div
        className="create-publish-media-frame create-publish-media-working"
        role="status"
      >
        <div className="create-publish-media-skeleton" />
        <span>
          <LoaderCircle /> Loading media…
        </span>
      </div>
    );
  }

  if (media?.generation_status === "ready" && source) {
    return (
      <div className="create-publish-media-frame create-publish-media-ready">
        {media.media_type === "video" ? (
          <video src={source} controls preload="metadata" />
        ) : (
          <img src={source} alt={media.alt_text || "Post media preview"} />
        )}
      </div>
    );
  }

  if (media && media.generation_status !== "ready") {
    return (
      <div
        className="create-publish-media-frame create-publish-media-failed"
        role="alert"
      >
        <AlertTriangle />
        <strong>This media is not available for publishing.</strong>
        <p>Upload an image or video to continue.</p>

        <div>
          <Button
            variant="secondary"
            className="btn-sm"
            onClick={onChooseUpload}
          >
            <Upload /> Upload media
          </Button>

          {fallbackMedia && (
            <Button
              variant="tertiary"
              className="btn-sm"
              onClick={() => onUseMedia(fallbackMedia)}
            >
              Use previous upload
            </Button>
          )}

          <Button
            variant="tertiary"
            className="btn-sm"
            onClick={() => onUseMedia(null)}
          >
            Continue without media
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="create-publish-media-frame create-publish-media-empty">
      <ImagePlus />
      <strong>Upload your post media</strong>
      <p>Add the image or video you want 9D Brain to market and publish.</p>

      <div>
        <Button
          variant="secondary"
          className="btn-sm"
          onClick={onChooseUpload}
        >
          <Upload /> Upload media
        </Button>
      </div>
    </div>
  );
}

function PublishResults({
  platforms,
  progress,
}: {
  platforms: CreatePublishPlatform[];
  progress: PublishProgress;
}) {
  if (platforms.every((platform) => progress[platform] === "idle")) return null;
  const labels: Record<PublishProgress[CreatePublishPlatform], string> = {
    idle: "Ready",
    approving: "Confirming…",
    publishing: "Publishing…",
    published: "Published",
    queued: "Queued safely",
    connection_required: "Connection required",
    coming_soon: "Coming soon",
    failed: "Needs attention",
  };
  return (
    <div className="create-publish-results" aria-live="polite">
      <h3>Publishing</h3>
      {platforms.map((platform) => (
        <div key={platform} data-state={progress[platform]}>
          <span>{PLATFORM_LABELS[platform]}</span>
          <strong>{labels[progress[platform]]}</strong>
        </div>
      ))}
    </div>
  );
}

function creativeHistoryStatus(status: CreativeAsset["generation_status"]) {
  if (status === "ready") return "Ready";
  if (status === "archived") return "Archived";
  return "Unavailable";
}

async function uploadWithDuration(
  file: File,
  onUpload: (file: File, durationSeconds?: number) => Promise<void>,
) {
  if (!file.type.startsWith("video/")) {
    await onUpload(file);
    return;
  }
  const url = URL.createObjectURL(file);
  try {
    const duration = await new Promise<number | undefined>((resolve) => {
      const video = document.createElement("video");
      video.preload = "metadata";
      video.onloadedmetadata = () => resolve(Math.max(1, Math.round(video.duration)));
      video.onerror = () => resolve(undefined);
      video.src = url;
    });
    await onUpload(file, duration);
  } finally {
    URL.revokeObjectURL(url);
  }
}
