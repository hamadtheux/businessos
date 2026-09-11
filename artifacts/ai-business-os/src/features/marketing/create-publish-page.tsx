import {
  AlertCircle,
  CalendarClock,
  Check,
  FilePenLine,
  ImagePlus,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation } from "wouter";

import { useBusiness } from "@/business-context";
import { Badge, Button, Modal, PageHeader } from "@/components/product-ui";
import {
  CreatePublishComposer,
  type CreatePublishMode,
} from "@/features/marketing/create-publish-composer";
import {
  CreatePublishEditor,
  type PublishProgress,
} from "@/features/marketing/create-publish-editor";
import {
  CREATE_PUBLISH_PLATFORMS,
  PLATFORM_LABELS,
  contentPlatform,
  customerStatus,
  localDateTimeToUtcIso,
  localDateTimeValueInZone,
  packageIdFromContent,
  platformReadiness,
  updatedPlatformFields,
  type EditablePlatformFields,
} from "@/features/marketing/create-publish-model";
import { CmoDepartmentNav } from "@/features/marketing/marketing-pages";
import { humanizeApiError } from "@/services/api-client";
import type { CreativeAsset, MarketingContent } from "@/services/api-types";
import { automationsApi } from "@/services/automations";
import { integrationsApi } from "@/services/integrations";
import {
  marketingApi,
  type ContentPackage,
  type CreatePublishPlatform,
  type GenerateContentPackageInput,
  type ManualContentPackageInput,
} from "@/services/marketing";

const EMPTY_PUBLISH_PROGRESS = Object.fromEntries(
  CREATE_PUBLISH_PLATFORMS.map((platform) => [platform, "idle"]),
) as PublishProgress;

export function CreatePublishPage() {
  const [, navigate] = useLocation();
  const { activeBusinessId, activeBusiness } = useBusiness();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<CreatePublishMode | null>(null);
  const [contentPackage, setContentPackage] = useState<ContentPackage | null>(null);
  const [activePlatform, setActivePlatform] = useState<CreatePublishPlatform>("instagram");
  const [media, setMedia] = useState<CreativeAsset | null>(null);
  const [mediaPreviewUrl, setMediaPreviewUrl] = useState<string | null>(null);
  const [creationStage, setCreationStage] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [draftState, setDraftState] = useState<"saved" | "saving" | "error">("saved");
  const [publishProgress, setPublishProgress] = useState<PublishProgress>(EMPTY_PUBLISH_PROGRESS);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const canApproveExternal = ["owner", "admin"].includes(activeBusiness?.membershipRole || "");

  useEffect(() => () => {
    if (mediaPreviewUrl?.startsWith("blob:")) URL.revokeObjectURL(mediaPreviewUrl);
  }, [mediaPreviewUrl]);

  const recent = useQuery({
    queryKey: ["marketing", activeBusinessId, "content", "create-publish"],
    queryFn: ({ signal }) =>
      marketingApi.content.list(activeBusinessId, { pageSize: 8 }, {}, signal),
    enabled: Boolean(activeBusinessId),
  });
  const definitions = useQuery({
    queryKey: ["integrations", activeBusinessId, "registry", "create-publish"],
    queryFn: ({ signal }) => integrationsApi.registry(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
  });
  const connections = useQuery({
    queryKey: ["integrations", activeBusinessId, "connections", "create-publish"],
    queryFn: ({ signal }) => integrationsApi.connections(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
  });
  const mediaHistory = useQuery({
    queryKey: ["marketing", activeBusinessId, "create-publish-media-history", contentPackage?.package_id],
    queryFn: ({ signal }) => {
      const owner = mediaOwner(contentPackage!.contents);
      return marketingApi.creative.list(
        activeBusinessId,
        undefined,
        undefined,
        signal,
        owner.root_content_id,
      );
    },
    enabled: Boolean(activeBusinessId && contentPackage?.contents.length),
  });

  const readiness = useMemo(
    () => Object.fromEntries(
      CREATE_PUBLISH_PLATFORMS.map((platform) => [
        platform,
        platformReadiness(
          platform,
          definitions.data,
          connections.data,
          definitions.isPending || connections.isPending,
        ),
      ]),
    ) as Record<CreatePublishPlatform, ReturnType<typeof platformReadiness>>,
    [connections.data, connections.isPending, definitions.data, definitions.isPending],
  );

  const upload = useMutation({
    mutationFn: ({ file, duration, contentId }: { file: File; duration?: number; contentId?: string }) =>
      marketingApi.creative.upload(activeBusinessId, file, duration, contentId),
    onError: (reason) => {
      setError(humanizeApiError(reason, "This media could not be uploaded. Choose another file."));
    },
  });

  const uploadMedia = async (file: File, duration?: number) => {
    setError("");
    const currentContent = contentPackage ? mediaOwner(contentPackage.contents) : undefined;
    const nextPreview = URL.createObjectURL(file);
    const priorPreview = mediaPreviewUrl;
    setMediaPreviewUrl(nextPreview);
    try {
      const asset = await upload.mutateAsync({
        file,
        duration,
        contentId: currentContent?.id,
      });
      await persistMediaChoice(asset);
      void queryClient.invalidateQueries({ queryKey: ["marketing", activeBusinessId, "create-publish-media-history"] });
      if (priorPreview?.startsWith("blob:")) URL.revokeObjectURL(priorPreview);
    } catch {
      URL.revokeObjectURL(nextPreview);
      setMediaPreviewUrl(priorPreview);
    }
  };

  async function persistMediaChoice(asset: CreativeAsset | null) {
    setMedia(asset);
    if (!contentPackage) return;
    const owner = mediaOwner(contentPackage.contents);
    setDraftState("saving");
    try {
      const saved = await marketingApi.content.edit(activeBusinessId, owner.id, {
        title: owner.title,
        body: owner.body,
        cta: owner.cta,
        platform_fields: {
          ...(owner.platform_fields || {}),
          media_disabled: asset === null,
          selected_media_asset_id: asset?.id || null,
        },
      });
      setContentPackage((current) => current ? {
        ...current,
        contents: current.contents.map((item) => item.id === owner.id ? saved : item),
      } : current);
      setDraftState("saved");
    } catch (reason) {
      setDraftState("error");
      setError(humanizeApiError(reason, "Your media preference could not be saved."));
    }
  }

  const createPackage = useMutation({
    mutationFn: async (input: GenerateContentPackageInput) => {
      setCreationStage("Creating your post…");

      const result = await marketingApi.content.packages.generate(
        activeBusinessId,
        input,
      );

      return {
        result,
        asset:
          input.media_asset_id && media?.id === input.media_asset_id
            ? media
            : null,
      };
    },
    onSuccess: ({ result, asset }) => {
      openPackage(result, asset);
      setNotice(
        asset
          ? "Your platform-ready post is ready with your uploaded media."
          : "Your platform-ready post is saved.",
      );
    },
    onError: (reason) =>
      setError(
        humanizeApiError(
          reason,
          "We couldn’t create this post. Your input is safe—try again.",
        ),
      ),
    onSettled: () => setCreationStage(""),
  });

  const manualPackage = useMutation({
    mutationFn: (input: ManualContentPackageInput) =>
      marketingApi.content.packages.createManual(activeBusinessId, input),
    onSuccess: (result) => {
      openPackage(result, media);
      setNotice("Your draft is ready. No AI rewriting was applied.");
    },
    onError: (reason) => setError(humanizeApiError(reason, "Your draft could not be saved.")),
  });

  const saveDraft = useMutation({
    mutationFn: ({ content, fields }: { content: MarketingContent; fields: EditablePlatformFields }) => {
      setDraftState("saving");
      return marketingApi.content.edit(activeBusinessId, content.id, {
        title: fields.title.trim(),
        body: fields.body.trim(),
        cta: fields.cta.trim() || null,
        platform_fields: updatedPlatformFields(content, fields),
      });
    },
    onSuccess: (saved) => {
      setContentPackage((current) => current ? {
        ...current,
        contents: current.contents.map((item) =>
          contentPlatform(item) === contentPlatform(saved) ? saved : item,
        ),
      } : current);
      setDraftState("saved");
    },
    onError: () => setDraftState("error"),
  });

  const rewrite = useMutation({
    mutationFn: ({ content, instruction }: { content: MarketingContent; instruction: string }) =>
      marketingApi.content.generate(activeBusinessId, {
        prompt: `${instruction}\n\nSource copy:\n${content.body}`,
        channel: content.channel,
        content_type: content.content_type,
        campaign_id: content.campaign_id,
        title: content.title,
        language: content.language,
        parent_content_id: content.id,
      }),
    onSuccess: (saved) => {
      setContentPackage((current) => current ? {
        ...current,
        contents: current.contents.map((item) =>
          contentPlatform(item) === contentPlatform(saved) ? saved : item,
        ),
      } : current);
      setNotice(`${PLATFORM_LABELS[contentPlatform(saved)]} copy was rewritten. Other variants were unchanged.`);
    },
    onError: (reason) => setError(humanizeApiError(reason, "This copy could not be rewritten.")),
  });


  const schedule = useMutation({
    mutationFn: async (scheduledFor: Record<CreatePublishPlatform, string>) => {
      if (!contentPackage) throw new Error("No post selected");
      return Promise.all(contentPackage.contents.map(async (content) => {
        const approved = await ensureContentApproved(activeBusinessId, content);
        return marketingApi.calendar.create(
          activeBusinessId,
          approved.id,
          scheduledFor[contentPlatform(content)],
        );
      }));
    },
    onSuccess: () => {
      setScheduleOpen(false);
      setNotice("Added to your content calendar for every selected platform. This does not publish automatically.");
      void queryClient.invalidateQueries({ queryKey: ["marketing", activeBusinessId] });
    },
    onError: (reason) => setError(humanizeApiError(reason, "This post could not be added to the content calendar.")),
  });

  const publish = useMutation({
    mutationFn: async () => {
      if (!contentPackage) throw new Error("No post selected");

      const next = { ...EMPTY_PUBLISH_PROGRESS };
      let attempted = 0;
      let failed = 0;

      contentPackage.contents.forEach((content) => {
        const platform = contentPlatform(content);

        next[platform] =
          readiness[platform].state === "coming_soon"
            ? "coming_soon"
            : readiness[platform].canPublish
              ? "approving"
              : "connection_required";
      });

      setPublishProgress(next);

      await Promise.all(
        contentPackage.contents.map(async (content) => {
          const platform = contentPlatform(content);

          if (
            !readiness[platform].canPublish ||
            (platform !== "facebook" &&
              platform !== "instagram")
          ) {
            return;
          }

          attempted += 1;

          try {
            const approved =
              await ensureContentApproved(
                activeBusinessId,
                content,
              );

            const proposal =
              await marketingApi.content.preparePublish(
                activeBusinessId,
                approved.id,
                platform,
              );

            if (
              proposal.connector_state !==
                "ready_after_approval" ||
              !proposal.approval_id
            ) {
              failed += 1;
              updatePublishProgress(
                platform,
                "connection_required",
              );
              return;
            }

            updatePublishProgress(
              platform,
              "publishing",
            );

            if (
              proposal.approval_status ===
              "pending"
            ) {
              await automationsApi.approvals.approve(
                activeBusinessId,
                proposal.approval_id,
                "Publish confirmed in Create & Publish.",
              );
            }

            const refreshed =
              await marketingApi.content.preparePublish(
                activeBusinessId,
                approved.id,
                platform,
              );

            const state = publishState(
              refreshed.action_status,
            );

            updatePublishProgress(
              platform,
              state,
            );

            if (
              state === "failed" ||
              state === "connection_required"
            ) {
              failed += 1;
            }
          } catch {
            failed += 1;
            updatePublishProgress(
              platform,
              "failed",
            );
          }
        }),
      );

      return {
        attempted,
        failed,
      };
    },
    onSuccess: () =>
      setNotice(
        "Publishing was prepared only for connected, supported platforms. Results are shown below.",
      ),
    onError: (reason) =>
      setError(
        humanizeApiError(
          reason,
          "Publishing could not be started.",
        ),
      ),
  });

  const publishAndCampaign = useMutation({
    mutationFn: async () => {
      if (!contentPackage) {
        throw new Error("No post selected");
      }

      const owner = mediaOwner(
        contentPackage.contents,
      );

      const selectedMediaId =
        owner.platform_fields
          ?.selected_media_asset_id;

      if (
        !media ||
        media.id !== selectedMediaId ||
        media.business_id !== activeBusinessId ||
        media.source_type !== "import" ||
        media.generation_status !== "ready" ||
        !media.storage_reference ||
        media.content_id !== owner.id
      ) {
        throw new Error(
          "Select a ready uploaded image or video before preparing a paid campaign.",
        );
      }

      const organicResult =
        await publish.mutateAsync();

      if (!organicResult.attempted) {
        throw new Error(
          "Connect Facebook or Instagram before using Publish + Run Campaign.",
        );
      }

      if (organicResult.failed > 0) {
        throw new Error(
          "One or more organic publishes need attention. No paid campaign proposal was created. Any publish that already completed was not rolled back.",
        );
      }

      return marketingApi.campaigns.generate(
        activeBusinessId,
        {
          goal: (
            `Promote the approved post: ${owner.title}`
          ).slice(0, 500),
          name: (
            `${owner.title} · Campaign`
          ).slice(0, 200),
          audience_definition: null,
          channels: [
            "meta",
            "google_ads",
          ],
          planned_budget: "0",
          budget_mode: "daily",
          catalog_scope: "none",
          catalog_item_ids: [],
          media_asset_id: media.id,
          source_content_id: owner.id,
        },
      );
    },
    onSuccess: async (campaign) => {
      setError("");
      setNotice(
        "Your post was published and its paid campaign proposal is ready for review. No ad was launched and no spend occurred.",
      );

      await queryClient.invalidateQueries({
        queryKey: [
          "marketing",
          activeBusinessId,
        ],
      });

      navigate(
        `/marketing/campaigns?campaign=${encodeURIComponent(
          campaign.id,
        )}`,
      );
    },
    onError: (reason) => {
      setNotice(
        "No paid campaign was launched or charged.",
      );

      setError(
        humanizeApiError(
          reason,
          "The paid campaign proposal could not be prepared.",
        ),
      );
    },
  });

  const loadRecent = useMutation({
    mutationFn: async (content: MarketingContent) => {
      const packageId = packageIdFromContent(content);
      const result = packageId
        ? await marketingApi.content.packages.get(activeBusinessId, packageId)
        : { package_id: content.root_content_id, canonical_message: content.body, contents: [content] };
      const owner = mediaOwner(result.contents);
      const assets = await marketingApi.creative.list(
        activeBusinessId,
        undefined,
        undefined,
        undefined,
        owner.root_content_id,
      );
      const selectedMediaId =
        owner.platform_fields?.selected_media_asset_id;
      const selected = selectedMediaId
        ? assets.find((asset) => asset.id === selectedMediaId) || null
        : assets[0] || null;
      return {
        result,
        asset:
          owner.platform_fields?.media_disabled === true
            ? null
            : selected,
      };
    },
    onSuccess: ({ result, asset }) => openPackage(result, asset),
    onError: (reason) => setError(humanizeApiError(reason, "This draft could not be opened.")),
  });

  function updatePublishProgress(
    platform: CreatePublishPlatform,
    state: PublishProgress[CreatePublishPlatform],
  ) {
    setPublishProgress((current) => ({ ...current, [platform]: state }));
  }

  function openPackage(result: ContentPackage, asset: CreativeAsset | null) {
    setContentPackage(result);
    setMedia(asset);
    setMode(null);
    setActivePlatform(contentPlatform(result.contents[0]));
    setPublishProgress({ ...EMPTY_PUBLISH_PROGRESS });
    setError("");
    void queryClient.invalidateQueries({ queryKey: ["marketing", activeBusinessId, "content"] });
  }

  const resetComposer = () => {
    setMode(null);
    setContentPackage(null);
    setMedia(null);
    setMediaPreviewUrl(null);
    setError("");
    setNotice("");
  };

  const currentMedia = media;

  const campaignAvailable = Boolean(
    currentMedia &&
      currentMedia.business_id === activeBusinessId &&
      currentMedia.source_type === "import" &&
      currentMedia.generation_status === "ready" &&
      currentMedia.storage_reference,
  );

  const actionPending =
    publish.isPending ||
    publishAndCampaign.isPending ||
    schedule.isPending;

  return (
    <div className="create-publish-page">
      <PageHeader
        eyebrow="Marketing"
        title="Create & Publish"
        subtitle="Turn an idea or existing media into platform-ready content."
        action={contentPackage ? (
          <Button variant="secondary" onClick={resetComposer}><Sparkles /> New post</Button>
        ) : undefined}
      />
      <CmoDepartmentNav active="Create & Publish" />
      {notice && <div className="ai-banner" role="status" aria-live="polite"><Check /> {notice}<button className="close-btn" onClick={() => setNotice("")} aria-label="Dismiss"><X /></button></div>}
      {error && <div className="ai-banner" role="alert"><AlertCircle /> {error}<button className="close-btn" onClick={() => setError("")} aria-label="Dismiss"><X /></button></div>}

      {contentPackage ? (
        <CreatePublishEditor
          contents={contentPackage.contents}
          activePlatform={activePlatform}
          readiness={readiness}
          media={currentMedia}
          mediaHistory={mediaHistory.data || (currentMedia ? [currentMedia] : [])}
          mediaLoading={upload.isPending}
          mediaPreviewUrl={mediaPreviewUrl}
          draftState={draftState}
          publishProgress={publishProgress}
          actionPending={actionPending}
          campaignPending={publishAndCampaign.isPending}
          campaignAvailable={campaignAvailable}
          canApproveExternal={canApproveExternal}
          onPlatformChange={setActivePlatform}
          onSave={(content, fields) => saveDraft.mutate({ content, fields })}
          onRewrite={(content, instruction) => rewrite.mutate({ content, instruction })}
          onUseMedia={(asset) => void persistMediaChoice(asset)}
          onUpload={uploadMedia}
          onConnect={() => navigate("/integrations")}
          onSchedule={() => setScheduleOpen(true)}
          onPublish={() => publish.mutate()}
          onPublishAndCampaign={() =>
            publishAndCampaign.mutate()
          }
        />
      ) : mode ? (
        <CreatePublishComposer
          mode={mode}
          readiness={readiness}
          uploadedAsset={media}
          mediaPreviewUrl={mediaPreviewUrl}
          pending={createPackage.isPending || manualPackage.isPending}
          uploading={upload.isPending}
          progressLabel={creationStage}
          error={error}
          onBack={resetComposer}
          onUpload={uploadMedia}
          onGenerate={(input) => createPackage.mutate(input)}
          onManual={(input) => manualPackage.mutate(input)}
        />
      ) : (
        <StartExperience
          recent={recent.data?.items || []}
          loading={recent.isLoading || loadRecent.isPending}
          onChoose={setMode}
          onOpenRecent={(content) => loadRecent.mutate(content)}
        />
      )}

      {scheduleOpen && contentPackage && (
        <ScheduleModal
          timezone={activeBusiness?.timezone || "UTC"}
          platforms={contentPackage.contents.map(contentPlatform)}
          pending={schedule.isPending}
          onClose={() => setScheduleOpen(false)}
          onSubmit={(value) => schedule.mutate(value)}
        />
      )}
    </div>
  );
}

function StartExperience({
  recent,
  loading,
  onChoose,
  onOpenRecent,
}: {
  recent: MarketingContent[];
  loading: boolean;
  onChoose: (mode: CreatePublishMode) => void;
  onOpenRecent: (content: MarketingContent) => void;
}) {
  const socialRecent = recent.filter((content) => Boolean(
    packageIdFromContent(content) ||
    content.platform_fields?.platform ||
    ["instagram", "facebook", "linkedin", "tiktok"].includes(content.channel),
  ));
  const uniqueRecent = socialRecent.filter((content, index, items) => {
    const packageId = packageIdFromContent(content);
    return !packageId || items.findIndex((item) => packageIdFromContent(item) === packageId) === index;
  });
  return (
    <>
      <section className="create-publish-start" aria-label="Choose how to create a post">
        <button type="button" onClick={() => onChoose("ai")} data-testid="start-ai">
          <span><Sparkles /></span>
          <strong>AI Copy & Strategy</strong>
          <p>Describe your goal. 9D Brain prepares the copy, strategy, and platform variants for review.</p>
        </button>
        <button type="button" onClick={() => onChoose("upload")} data-testid="start-upload">
          <span><Upload /></span>
          <strong>Upload Media</strong>
          <p>Bring a photo or video. 9D Brain prepares the platform-ready post.</p>
        </button>
        <button type="button" onClick={() => onChoose("manual")} data-testid="start-manual">
          <span><FilePenLine /></span>
          <strong>Create Manually</strong>
          <p>Write it yourself, keep full control, and publish when you&apos;re ready.</p>
        </button>
      </section>
      <section className="create-publish-recent">
        <div className="create-publish-section-head">
          <div><h2>Recent drafts</h2><p>Continue where you left off.</p></div>
          <Button variant="tertiary" className="btn-sm" onClick={() => onChoose("ai")}><ImagePlus /> New post</Button>
        </div>
        {loading ? (
          <div className="create-publish-recent-skeleton"><span /><span /><span /></div>
        ) : uniqueRecent.length ? (
          <div className="create-publish-recent-list">
            {uniqueRecent.slice(0, 6).map((content) => (
              <button key={content.id} type="button" onClick={() => onOpenRecent(content)}>
                <span className="create-publish-recent-thumb">{PLATFORM_LABELS[contentPlatform(content)].slice(0, 1)}</span>
                <span><strong>{content.title}</strong><small>{PLATFORM_LABELS[contentPlatform(content)]} · Updated {new Date(content.updated_at).toLocaleDateString()}</small></span>
                <Badge tone={customerStatus(content) === "Ready" ? "success" : "neutral"}>{customerStatus(content)}</Badge>
              </button>
            ))}
          </div>
        ) : (
          <div className="create-publish-recent-empty"><p>No drafts yet. Choose a starting point above.</p></div>
        )}
      </section>
    </>
  );
}

function ScheduleModal({
  timezone,
  platforms,
  pending,
  onClose,
  onSubmit,
}: {
  timezone: string;
  platforms: CreatePublishPlatform[];
  pending: boolean;
  onClose: () => void;
  onSubmit: (value: Record<CreatePublishPlatform, string>) => void;
}) {
  let minimumValue = "";
  let defaultValue = "";
  let timezoneError = "";
  try {
    minimumValue = localDateTimeValueInZone(new Date(), timezone);
    defaultValue = localDateTimeValueInZone(
      new Date(Date.now() + 86_400_000),
      timezone,
    );
  } catch (reason) {
    timezoneError = reason instanceof Error
      ? reason.message
      : "The business timezone is invalid. Update it in Business Settings.";
  }
  const [timing, setTiming] = useState<"same" | "per_platform">("same");
  const [timeError, setTimeError] = useState("");
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const values = Object.fromEntries(platforms.map((platform) => {
        const value = String(
          form.get(
            timing === "same"
              ? "scheduled_for"
              : `scheduled_for_${platform}`,
          ) || "",
        );
        return [platform, localDateTimeToUtcIso(value, timezone)];
      })) as Record<CreatePublishPlatform, string>;

      setTimeError("");
      onSubmit(values);
    } catch (reason) {
      setTimeError(
        reason instanceof Error
          ? reason.message
          : "Choose a valid date and time.",
      );
    }
  };
  return (
    <Modal title="Schedule in Content Calendar" description={`Choose when this post should appear in your 9D Brain content calendar for ${platforms.length} platform${platforms.length === 1 ? "" : "s"}. This does not publish automatically.`} onClose={onClose}>
      <form onSubmit={submit} className="create-publish-schedule-form">
        <fieldset className="create-publish-timing-choice">
          <legend>Timing</legend>
          <label><input type="radio" name="timing" value="same" checked={timing === "same"} onChange={() => setTiming("same")} /> Same time for all platforms</label>
          <label><input type="radio" name="timing" value="per_platform" checked={timing === "per_platform"} onChange={() => setTiming("per_platform")} /> Choose per platform</label>
        </fieldset>
        {timing === "same" ? (
          <label className="create-publish-field"><span>Date &amp; time</span><input type="datetime-local" name="scheduled_for" min={minimumValue} defaultValue={defaultValue} required /></label>
        ) : platforms.map((platform) => (
          <label className="create-publish-field" key={platform}><span>{PLATFORM_LABELS[platform]}</span><input type="datetime-local" name={`scheduled_for_${platform}`} min={minimumValue} defaultValue={defaultValue} required /></label>
        ))}
        <label className="create-publish-field"><span>Timezone</span><input value={timezone} readOnly /></label>
        {(timezoneError || timeError) && (
          <p className="create-publish-form-error" role="alert">
            {timezoneError || timeError}
          </p>
        )}
        <div className="modal-foot">
          <Button type="button" variant="tertiary" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={pending || Boolean(timezoneError)}><CalendarClock /> {pending ? "Scheduling…" : "Schedule in Calendar"}</Button>
        </div>
      </form>
    </Modal>
  );
}

function mediaOwner(contents: MarketingContent[]) {
  return contents.find((content) => contentPlatform(content) === "instagram") || contents[0];
}

async function ensureContentApproved(businessId: string, content: MarketingContent) {
  if (["approved", "scheduled", "ready_to_publish"].includes(content.status)) return content;
  const reviewed = content.status === "draft"
    ? await marketingApi.content.status(businessId, content.id, "review")
    : content;
  return marketingApi.content.status(businessId, reviewed.id, "approved");
}

function publishState(actionStatus: string): PublishProgress[CreatePublishPlatform] {
  if (actionStatus === "succeeded") return "published";
  if (actionStatus === "queued") return "queued";
  if (actionStatus === "executing") return "publishing";
  if (["failed", "uncertain", "rejected", "expired", "canceled"].includes(actionStatus)) return "failed";
  return "queued";
}
