import { useState, type FormEvent } from "react";
import { AlertCircle, Check, RefreshCw, Sparkles, Target } from "lucide-react";
import { Badge, Button, WorkspaceDrawer } from "@/components/product-ui";
import {
  AUDIENCE_GUIDANCE_MAX,
  OFFER_MAX,
  OWNER_GOAL_MAX,
  type CampaignGenerationInput,
} from "@/lib/cmo-ux";
import type {
  MarketingCampaign,
  MarketingChannel,
  MarketingContentType,
} from "@/services/api-types";

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
const contentTypes: MarketingContentType[] = [
  "social_post",
  "ad_copy",
  "email_draft",
  "whatsapp_draft",
  "blog_draft",
  "landing_page_copy",
  "headline",
  "cta",
  "content_package",
];
const primaryPlatforms: MarketingChannel[] = [
  "instagram",
  "facebook",
  "linkedin",
  "tiktok",
];
const channelLabels: Record<MarketingChannel, string> = {
  meta: "Meta",
  google_ads: "Google Ads",
  instagram: "Instagram",
  facebook: "Facebook",
  linkedin: "LinkedIn",
  tiktok: "TikTok",
  email: "Email",
  whatsapp: "WhatsApp",
  website: "Website",
  other: "Other",
};

type Props = {
  open: boolean;
  canAuthorizeOffer: boolean;
  businessName?: string;
  businessLocale?: string;
  campaigns?: MarketingCampaign[];
  campaignsLoading: boolean;
  campaignsError: boolean;
  pending: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (input: CampaignGenerationInput) => void;
};

export function CmoContentGeneratorDrawer({
  open,
  canAuthorizeOffer,
  businessName,
  businessLocale,
  campaigns,
  campaignsLoading,
  campaignsError,
  pending,
  error,
  onClose,
  onSubmit,
}: Props) {
  const [validationError, setValidationError] = useState("");

  const close = () => {
    setValidationError("");
    onClose();
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const selected = form.getAll("platforms").map(String) as MarketingChannel[];
    const additional = String(form.get("additional_channel") || "") as MarketingChannel;
    if (additional) selected.push(additional);
    const offer = String(form.get("offer") || "").trim();
    const offerAuthorized = form.get("offer_authorized") === "on";
    if (offer && !canAuthorizeOffer) {
      setValidationError(
        "Owner or administrator access is required to authorize a promotional offer.",
      );
      return;
    }
    if (offer && !offerAuthorized) {
      setValidationError("Confirm that this offer is authorized for this business.");
      return;
    }
    setValidationError("");
    onSubmit({
      channels: [...new Set(selected)],
      goal: String(form.get("prompt") || "").trim(),
      audience: String(form.get("audience") || "").trim(),
      offer,
      offerAuthorized,
      contentType: String(form.get("content_type") || "social_post") as MarketingContentType,
      campaignId: String(form.get("campaign_id")) || null,
      title: String(form.get("title")) || null,
      language: String(form.get("language") || "en"),
    });
  };

  return (
    <WorkspaceDrawer
      open={open}
      eyebrow="AI CMO"
      title="Create campaign content"
      description="Share the goal in plain language. 9D Brain handles the marketing strategy, copy, and channel adaptation."
      onClose={close}
      closeDisabled={pending}
      testId="cmo-content-workspace-drawer"
      footer={
        <div className="cmo-drawer-footer-actions">
          <Button type="button" disabled={pending} onClick={close}>
            Cancel
          </Button>
          <Button
            variant="primary"
            type="submit"
            form="cmo-content-generator-form"
            disabled={pending}
          >
            {pending ? (
              <><RefreshCw className="spin" />Creating draft…</>
            ) : (
              <><Sparkles />Create campaign content</>
            )}
          </Button>
        </div>
      }
    >
      <form id="cmo-content-generator-form" className="cmo-drawer-form" onSubmit={submit}>
        <div className="cmo-drawer-intro">
          <div>
            <div className="eyebrow">Grounded generation</div>
            <h3>{businessName ? `Create for ${businessName}` : "Create marketing content"}</h3>
            <p>
              AI CMO uses trusted Business Brain context and authorized business
              campaign input. Industry privacy rules remain enforced.
            </p>
          </div>
          <div className="chip-list">
            <Badge tone="info"><Sparkles />AI CMO</Badge>
            <Badge tone="success"><Check />Review first</Badge>
          </div>
        </div>

        <section className="cmo-form-section" aria-labelledby="cmo-content-brief-heading">
          <div className="cmo-form-section-heading">
            <h3 id="cmo-content-brief-heading">Campaign brief</h3>
            <p>Set the outcome and add audience or offer guidance only when needed.</p>
          </div>
          <div className="cmo-drawer-grid">
            <div className="field full">
              <label htmlFor="cmo-content-prompt">What do you want to achieve?</label>
              <textarea
                id="cmo-content-prompt"
                name="prompt"
                required
                maxLength={OWNER_GOAL_MAX}
                autoFocus
                placeholder="Example: Promote our new shoes"
                className="cmo-goal-input"
              />
              <span className="cmo-field-help">
                A short request is enough. Unsupported AI-generated claims remain blocked.
              </span>
            </div>
            <div className="field full">
              <label htmlFor="cmo-content-audience">Audience <span className="cmo-optional">optional</span></label>
              <input
                id="cmo-content-audience"
                name="audience"
                maxLength={AUDIENCE_GUIDANCE_MAX}
                placeholder="Let 9D Brain choose"
              />
            </div>
            <div className="field full">
              <label htmlFor="cmo-content-offer">Offer <span className="cmo-optional">optional</span></label>
              <input
                id="cmo-content-offer"
                name="offer"
                maxLength={OFFER_MAX}
                placeholder="Example: 50% off"
                disabled={!canAuthorizeOffer}
                onChange={() => setValidationError("")}
              />
              <span className="cmo-field-help">
                {canAuthorizeOffer
                  ? "Authorized business offers are provenance-marked and stay approval protected."
                  : "Owner or administrator access is required to authorize a promotional offer."}
              </span>
              {canAuthorizeOffer && (
                <label className="check-line" htmlFor="cmo-content-offer-authorized">
                  <input
                    id="cmo-content-offer-authorized"
                    name="offer_authorized"
                    type="checkbox"
                    onChange={() => setValidationError("")}
                  />
                  I confirm this offer is authorized for this business.
                </label>
              )}
            </div>
          </div>
        </section>

        <section className="cmo-form-section" aria-labelledby="cmo-content-platforms-heading">
          <div className="cmo-form-section-heading">
            <h3 id="cmo-content-platforms-heading">Platforms</h3>
            <p>Each selected platform receives its own native copy variant.</p>
          </div>
          <div className="cmo-channel-grid" aria-label="Campaign platforms">
            {primaryPlatforms.map((channel) => (
              <label className="cmo-channel-option" key={channel}>
                <input type="checkbox" name="platforms" value={channel} defaultChecked={channel === "instagram"} />
                <span>{channelLabels[channel]}</span>
              </label>
            ))}
          </div>
        </section>

        <details className="cmo-advanced-controls">
          <summary>Advanced controls</summary>
          <div className="cmo-drawer-grid">
            <div className="field">
              <label htmlFor="cmo-content-campaign">Campaign</label>
              <select id="cmo-content-campaign" name="campaign_id" disabled={campaignsLoading} defaultValue="">
                <option value="">{campaignsLoading ? "Loading campaigns…" : "Standalone content"}</option>
                {campaigns?.map((campaign) => <option value={campaign.id} key={campaign.id}>{campaign.name}</option>)}
              </select>
              <span className="cmo-field-help">Optional campaign context</span>
            </div>
            <div className="field">
              <label htmlFor="cmo-content-channel">Additional channel</label>
              <select id="cmo-content-channel" name="additional_channel" defaultValue="">
                <option value="">None</option>
                {channels.map((channel) => <option key={channel} value={channel}>{channelLabels[channel]}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="cmo-content-type">Content type</label>
              <select id="cmo-content-type" name="content_type" defaultValue="social_post">
                {contentTypes.map((type) => <option key={type} value={type}>{type.replaceAll("_", " ")}</option>)}
              </select>
            </div>
            <div className="field">
              <label htmlFor="cmo-content-language">Language</label>
              <input id="cmo-content-language" name="language" defaultValue={businessLocale || "en"} maxLength={16} spellCheck={false} />
            </div>
            <div className="field full">
              <label htmlFor="cmo-content-title">Title override <span className="cmo-optional">optional</span></label>
              <input id="cmo-content-title" name="title" maxLength={180} placeholder="Leave blank and let AI CMO create the title" />
            </div>
          </div>
        </details>

        <div className="cmo-assurance-grid">
          <div className="cmo-assurance-item">
            <div className="cmo-assurance-title"><Check size={13} />Business Brain</div>
            <p>Uses trusted business facts and branding available to the CMO.</p>
          </div>
          <div className="cmo-assurance-item">
            <div className="cmo-assurance-title"><Target size={13} />Campaign aware</div>
            <p>Authorized offers retain server-classified provenance.</p>
          </div>
          <div className="cmo-assurance-item">
            <div className="cmo-assurance-title"><AlertCircle size={13} />Approval protected</div>
            <p>Generation creates a draft only. Nothing is published automatically.</p>
          </div>
        </div>
        {campaignsError && (
          <div className="ai-banner warning"><AlertCircle />Campaigns could not load. You can still create standalone content.</div>
        )}
        {(validationError || error) && (
          <p className="form-error" role="alert">{validationError || error}</p>
        )}
      </form>
    </WorkspaceDrawer>
  );
}
