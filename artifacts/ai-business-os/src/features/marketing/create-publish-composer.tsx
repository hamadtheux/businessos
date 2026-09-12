import {
  ArrowLeft,
  ChevronDown,
  FilePenLine,
  ImagePlus,
  Sparkles,
  Upload,
} from "lucide-react";
import { useRef, useState, type DragEvent, type FormEvent, type RefObject } from "react";

import { Button } from "@/components/product-ui";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  CREATE_PUBLISH_PLATFORMS,
  PLATFORM_LABELS,
  type PlatformReadiness,
} from "@/features/marketing/create-publish-model";
import type { CreativeAsset } from "@/services/api-types";
import type {
  CreatePublishPlatform,
  GenerateContentPackageInput,
  ManualContentPackageInput,
} from "@/services/marketing";

export type CreatePublishMode = "ai" | "upload" | "manual";

type ComposerProps = {
  mode: CreatePublishMode;
  readiness: Record<CreatePublishPlatform, PlatformReadiness>;
  uploadedAsset: CreativeAsset | null;
  mediaPreviewUrl: string | null;
  pending: boolean;
  uploading: boolean;
  progressLabel?: string;
  error?: string;
  onBack: () => void;
  onUpload: (file: File) => Promise<void>;
  onGenerate: (input: GenerateContentPackageInput) => void;
  onManual: (input: ManualContentPackageInput) => void;
};

export function CreatePublishComposer({
  mode,
  readiness,
  uploadedAsset,
  mediaPreviewUrl,
  pending,
  uploading,
  progressLabel,
  error,
  onBack,
  onUpload,
  onGenerate,
  onManual,
}: ComposerProps) {
  const [platforms, setPlatforms] = useState<CreatePublishPlatform[]>([
    "instagram",
    "facebook",
  ]);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const togglePlatform = (platform: CreatePublishPlatform) => {
    setPlatforms((current) =>
      current.includes(platform)
        ? current.filter((item) => item !== platform)
        : [...current, platform],
    );
  };

  const handleFiles = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    await onUpload(file);
  };

  const drop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    void handleFiles(event.dataTransfer.files);
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (
      !platforms.length ||
      (uploadedAsset && uploadedAsset.generation_status !== "ready")
    ) return;
    const form = new FormData(event.currentTarget);
    if (mode === "manual") {
      onManual({
        post_text: String(form.get("post_text") || "").trim(),
        platforms,
        title: valueOrNull(form, "title"),
        cta: valueOrNull(form, "cta"),
        hashtags: splitHashTags(String(form.get("hashtags") || "")),
        keywords: splitKeywords(String(form.get("keywords") || "")),
        alt_text: valueOrNull(form, "alt_text"),
        media_asset_id: uploadedAsset?.id || null,
        language: String(form.get("language") || "en"),
      });
      return;
    }
    onGenerate(
      {
        goal: String(form.get("goal") || "").trim(),
        platforms,
        media_asset_id: uploadedAsset?.id || null,
        audience: valueOrNull(form, "audience"),
        tone: valueOrNull(form, "tone"),
        objective: valueOrNull(form, "objective"),
        language: String(form.get("language") || "en"),
      }
    );
  };

  const requiresMedia = mode === "upload";
  const title =
    mode === "ai"
      ? "AI Copy & Strategy"
      : mode === "upload"
        ? "Prepare uploaded media"
        : "Create manually";

  return (
    <section className="create-publish-composer" aria-labelledby="create-publish-mode-title">
      <button className="create-publish-back" type="button" onClick={onBack}>
        <ArrowLeft /> Create &amp; Publish
      </button>
      <div className="create-publish-composer-head">
        <div className="create-publish-mode-mark" aria-hidden="true">
          {mode === "ai" ? <Sparkles /> : mode === "upload" ? <Upload /> : <FilePenLine />}
        </div>
        <div>
          <h2 id="create-publish-mode-title">{title}</h2>
          <p>
            {mode === "manual"
              ? "Your words stay yours. AI optimization is optional after the draft is ready."
              : "Share the idea and choose where it should appear. 9D Brain handles the platform details."}
          </p>
        </div>
      </div>

      <form onSubmit={submit} className="create-publish-form">
        {(mode === "upload" || mode === "manual") && (
          <MediaDropzone
            required={requiresMedia}
            dragging={dragging}
            asset={uploadedAsset}
            previewUrl={mediaPreviewUrl}
            uploading={uploading}
            fileRef={fileRef}
            onDragEnter={() => setDragging(true)}
            onDragLeave={() => setDragging(false)}
            onDrop={drop}
            onChoose={() => fileRef.current?.click()}
            onFiles={handleFiles}
          />
        )}

        {mode === "manual" ? (
          <>
            <label className="create-publish-field create-publish-field-main">
              <span>Post text</span>
              <textarea
                name="post_text"
                required
                maxLength={10000}
                placeholder="Write your post…"
              />
            </label>
            <div className="create-publish-optional-grid">
              <label className="create-publish-field">
                <span>Headline or title <small>Optional</small></span>
                <input name="title" maxLength={180} placeholder="Add a title" />
              </label>
              <label className="create-publish-field">
                <span>CTA <small>Optional</small></span>
                <input name="cta" maxLength={300} placeholder="Run smarter" />
              </label>
              <label className="create-publish-field">
                <span>Hashtags <small>Optional</small></span>
                <input name="hashtags" placeholder="#smallbusiness #growth" />
              </label>
              <label className="create-publish-field">
                <span>Keywords <small>Optional</small></span>
                <input name="keywords" placeholder="operations, growth" />
              </label>
            </div>
          </>
        ) : (
          <label className="create-publish-field create-publish-field-main">
            <span>{mode === "ai" ? "What would you like to promote?" : "What is this post about?"}</span>
            <textarea
              name="goal"
              required={mode === "ai"}
              maxLength={2400}
              placeholder={
                mode === "ai"
                  ? "Describe your goal…"
                  : "Optional — add context for the post"
              }
            />
            {mode === "ai" && (
              <div className="create-publish-suggestions" aria-label="Prompt suggestions">
                {["Promote a service", "Announce something", "Share an update", "Generate engagement"].map((label) => (
                  <button
                    type="button"
                    key={label}
                    onClick={(event) => {
                      const textarea = event.currentTarget
                        .closest("label")
                        ?.querySelector("textarea");
                      if (textarea) {
                        textarea.value = label;
                        textarea.focus();
                      }
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
          </label>
        )}

        <PlatformSelector
          platforms={platforms}
          readiness={readiness}
          onToggle={togglePlatform}
        />

        {uploadedAsset && (
          <p className="create-publish-auto-media-note">
            Your media is automatically optimized for each selected platform.
          </p>
        )}

        {mode !== "manual" && (
          <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
            <CollapsibleTrigger asChild>
              <button type="button" className="create-publish-advanced-trigger">
                Advanced settings <ChevronDown data-open={advancedOpen} />
              </button>
            </CollapsibleTrigger>
            <CollapsibleContent className="create-publish-advanced-content">
              <label className="create-publish-field">
                <span>Audience</span>
                <input name="audience" maxLength={400} placeholder="Choose for me" />
              </label>
              <label className="create-publish-field">
                <span>Tone</span>
                <select name="tone" defaultValue="">
                  <option value="">Use brand voice</option>
                  <option value="professional">Professional</option>
                  <option value="friendly">Friendly</option>
                  <option value="bold">Bold</option>
                  <option value="educational">Educational</option>
                </select>
              </label>
              <label className="create-publish-field">
                <span>Objective</span>
                <select name="objective" defaultValue="">
                  <option value="">Choose for me</option>
                  <option value="awareness">Awareness</option>
                  <option value="engagement">Engagement</option>
                  <option value="leads">Leads</option>
                  <option value="sales">Sales</option>
                </select>
              </label>

              <label className="create-publish-field">
                <span>Language</span>
                <select name="language" defaultValue="en">
                  <option value="en">English</option>
                  <option value="ur">Urdu</option>
                  <option value="ar">Arabic</option>
                </select>
              </label>
            </CollapsibleContent>
          </Collapsible>
        )}

        {mode === "manual" && <input type="hidden" name="language" value="en" />}
        {error && <p className="create-publish-form-error" role="alert">{error}</p>}
        {progressLabel && (
          <div
            className="create-publish-progress"
            data-processing={uploadedAsset?.generation_status === "processing"}
            role="status"
            aria-live="polite"
          >
            <span /> {progressLabel}
          </div>
        )}
        <div className="create-publish-form-foot">
          <p>{platforms.length ? `${platforms.length} platform${platforms.length === 1 ? "" : "s"} selected` : "Choose at least one platform"}</p>
          <Button
            variant="primary"
            type="submit"
            disabled={
              pending ||
              uploading ||
              !platforms.length ||
              (requiresMedia && !uploadedAsset) ||
              Boolean(
                uploadedAsset &&
                  uploadedAsset.generation_status !== "ready",
              )
            }
            data-testid="create-publish-submit"
          >
            {mode === "manual" ? <FilePenLine /> : mode === "upload" ? <ImagePlus /> : <Sparkles />}
            {pending ? "Preparing…" : mode === "manual" ? "Continue" : mode === "upload" ? "Generate platform content" : "Create Post"}
          </Button>
        </div>
      </form>
    </section>
  );
}

export function PlatformSelector({
  platforms,
  readiness,
  onToggle,
}: {
  platforms: CreatePublishPlatform[];
  readiness: Record<CreatePublishPlatform, PlatformReadiness>;
  onToggle: (platform: CreatePublishPlatform) => void;
}) {
  return (
    <fieldset className="create-publish-platform-fieldset">
      <legend>Platforms</legend>
      <div className="create-publish-platform-grid">
        {CREATE_PUBLISH_PLATFORMS.map((platform) => {
          const selected = platforms.includes(platform);
          const state = readiness[platform];
          return (
            <button
              key={platform}
              type="button"
              className="create-publish-platform"
              data-selected={selected}
              aria-pressed={selected}
              onClick={() => onToggle(platform)}
            >
              <span className="create-publish-platform-icon" aria-hidden="true">
                {PLATFORM_LABELS[platform].slice(0, 1)}
              </span>
              <span>
                <strong>{PLATFORM_LABELS[platform]}</strong>
                <small data-state={state.state}>{state.label}</small>
              </span>
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

function MediaDropzone({
  required,
  dragging,
  asset,
  previewUrl,
  uploading,
  fileRef,
  onDragEnter,
  onDragLeave,
  onDrop,
  onChoose,
  onFiles,
}: {
  required: boolean;
  dragging: boolean;
  asset: CreativeAsset | null;
  previewUrl: string | null;
  uploading: boolean;
  fileRef: RefObject<HTMLInputElement | null>;
  onDragEnter: () => void;
  onDragLeave: () => void;
  onDrop: (event: DragEvent<HTMLDivElement>) => void;
  onChoose: () => void;
  onFiles: (files: FileList | null) => Promise<void>;
}) {
  return (
    <div
      className="create-publish-dropzone"
      data-dragging={dragging}
      data-has-media={Boolean(asset)}
      data-uploading={uploading}
      data-processing={asset?.generation_status === "processing"}
      onDragOver={(event) => event.preventDefault()}
      onDragEnter={onDragEnter}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <input
        ref={fileRef}
        type="file"
        accept=".jpg,.jpeg,.png,.webp,.mp4,.mov,.webm,image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm"
        required={required && !asset}
        onChange={(event) => void onFiles(event.target.files)}
      />
      {asset ? (
        <>
          {previewUrl && (
            <div
              className="create-publish-upload-preview"
              data-processing={asset.generation_status === "processing"}
            >
              {asset.media_type === "video" ? (
                <video src={previewUrl} controls preload="metadata" />
              ) : (
                <img src={previewUrl} alt="Selected post media" />
              )}
            </div>
          )}
          <div>
            <strong>
              {asset.generation_status === "processing" ? (
                <span className="create-publish-motion-label">
                  Preparing video
                  <span className="create-publish-motion-dots" aria-hidden="true">
                    <i />
                    <i />
                    <i />
                  </span>
                </span>
              ) : asset.generation_status === "failed" ? (
                "Video needs a new upload"
              ) : (
                "Media ready"
              )}
            </strong>
            <p role={asset.generation_status === "failed" ? "alert" : "status"}>
              {asset.generation_status === "processing"
                ? "Preparing your video for each platform…"
                : asset.generation_status === "failed"
                  ? "We couldn’t prepare this video. Try uploading it again."
                  : `${asset.media_type === "video" ? "Video" : "Image"} · Stored privately`}
            </p>
            <Button type="button" className="btn-sm" onClick={onChoose}>Replace</Button>
          </div>
        </>
      ) : (
        <>
          <Upload />
          <div>
            <strong>
              {uploading ? (
                <span className="create-publish-motion-label">
                  Uploading media
                  <span className="create-publish-motion-dots" aria-hidden="true">
                    <i />
                    <i />
                    <i />
                  </span>
                </span>
              ) : (
                "Drop a photo or video here"
              )}
            </strong>
            <p>JPG, PNG, WEBP, MP4, MOV or WEBM</p>
            <Button type="button" className="btn-sm" onClick={onChoose} disabled={uploading}>
              Choose file
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

function valueOrNull(form: FormData, key: string) {
  const value = String(form.get(key) || "").trim();
  return value || null;
}

function splitHashTags(value: string) {
  return [...new Set(value.split(/\s+/).map((item) => item.trim()).filter(Boolean))];
}

function splitKeywords(value: string) {
  return [...new Set(value.split(/[,\n]+/).map((item) => item.trim()).filter(Boolean))];
}
