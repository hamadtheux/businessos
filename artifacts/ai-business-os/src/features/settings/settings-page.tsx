import { useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  Check,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useBusiness } from "@/business-context";
import { PRODUCT_NAME } from "@/config/brand";
import {
  Avatar,
  Badge,
  Button,
  Card,
  Modal,
  PageHeader,
  SectionTitle,
} from "@/components/product-ui";
import {
  BrandingEditor,
  brandingDraftHasErrors,
} from "@/features/branding/branding-editor";
import {
  createBrandIdentityDraft,
  hasCustomBrandColors,
  type BrandIdentityDraft,
} from "@/lib/brand-theme";
import {
  resetSettingsBranding,
  saveSettingsBranding,
} from "@/features/settings/settings-branding-save";
import { revokeBrandLogo } from "@/services/brand-logo";
import type { Business } from "@/types/business";
import { marketingApi } from "@/services/marketing";

function createProfileForm(business: Business) {
  return {
    name: business.name,
    industry: business.industry,
    website: business.website,
    location: business.location,
    timezone: business.timezone,
    currency: business.currency,
    description: business.description,
    tone: business.tone,
    avoidKeywords: business.avoidKeywords,
    products: business.products,
  };
}

export function SettingsPage() {
  const {
    activeBusiness,
    activeBusinessId,
    deleteLogo,
    updateBranding,
    updateBusiness,
    uploadLogo,
  } = useBusiness();
  const [tab, setTab] = useState("Business Profile");
  const [saved, setSaved] = useState("");
  const [error, setError] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);
  const canManageSpend = ["owner", "admin"].includes(
    activeBusiness?.membershipRole ?? "",
  );
  const spendPolicy = useQuery({
    queryKey: ["marketing", activeBusinessId, "spend-policy"],
    queryFn: ({ signal }) => marketingApi.spendPolicy.get(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId && canManageSpend),
  });
  const saveSpendPolicy = useMutation({
    mutationFn: (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const optionalMoney = (name: string) => {
        const value = String(form.get(name) ?? "").trim();
        return value || null;
      };
      return marketingApi.spendPolicy.update(activeBusinessId, {
        currency: activeBusiness?.currency ?? "USD",
        max_single_campaign_budget: String(form.get("max_single_campaign_budget")),
        max_single_budget_change: String(form.get("max_single_budget_change")),
        daily_advertising_limit: optionalMoney("daily_advertising_limit"),
        monthly_ai_managed_limit: optionalMoney("monthly_ai_managed_limit"),
        active: form.get("active") === "on",
        confirm_material_increase: form.get("confirm_material_increase") === "on",
      });
    },
    onSuccess: (value) => {
      setSaved("Server-owned advertising limits were saved and audit logged.");
      setError("");
      void spendPolicy.refetch();
      return value;
    },
    onError: (reason) => {
      setSaved("");
      setError(reason instanceof Error ? reason.message : "Spend policy could not be saved.");
    },
  });
  const [form, setForm] = useState(() =>
    activeBusiness ? createProfileForm(activeBusiness) : null,
  );
  const [brandDraft, setBrandDraft] = useState<BrandIdentityDraft>(() =>
    createBrandIdentityDraft(activeBusiness?.brandIdentity),
  );
  const [hasCustomBrand, setHasCustomBrand] = useState(
    hasSourceColors(activeBusiness),
  );
  const syncedBusinessId = useRef(activeBusiness?.id ?? "");
  const brandingDraftIsDirty = useRef(false);
  const pendingLogo = useRef(brandDraft.logo);
  pendingLogo.current = brandDraft.logo;

  useEffect(
    () => () => {
      revokeBrandLogo(pendingLogo.current);
    },
    [],
  );

  useEffect(() => {
    if (!activeBusiness) return;
    const switchedBusiness = syncedBusinessId.current !== activeBusiness.id;
    if (switchedBusiness) {
      revokeBrandLogo(brandDraft.logo);
      syncedBusinessId.current = activeBusiness.id;
      brandingDraftIsDirty.current = false;
      setForm(createProfileForm(activeBusiness));
      setSaved("");
      setError("");
    }
    if (switchedBusiness || !brandingDraftIsDirty.current) {
      setBrandDraft(createBrandIdentityDraft(activeBusiness.brandIdentity));
      setHasCustomBrand(hasSourceColors(activeBusiness));
    }
  }, [
    activeBusiness?.id,
    activeBusiness?.brandIdentity?.primaryColor,
    activeBusiness?.brandIdentity?.secondaryColor,
    activeBusiness?.brandIdentity?.accentColor,
    activeBusiness?.brandIdentity?.logoUrl,
    activeBusiness?.brandIdentity?.logo?.previewUrl,
  ]);

  if (!activeBusiness || !form) {
    return (
      <div className="empty">
        <AlertCircle />
        <h3>No active business</h3>
        <p>Select or create a business before changing settings.</p>
      </div>
    );
  }

  const change = <K extends keyof typeof form>(
    key: K,
    value: (typeof form)[K],
  ) => setForm((current) => (current ? { ...current, [key]: value } : current));

  const save = async () => {
    setError("");
    setSaved("");
    if (hasCustomBrand && brandingDraftHasErrors(brandDraft)) {
      setTab("Branding");
      setError("Check the brand HEX values before saving.");
      return;
    }
    try {
      const savedBusiness = await saveSettingsBranding(
        activeBusiness.id,
        brandDraft,
        hasCustomBrand,
        updateBranding,
        uploadLogo,
        deleteLogo,
        activeBusiness.brandIdentity?.logoUrl,
      );
      revokeBrandLogo(brandDraft.logo);
      brandingDraftIsDirty.current = false;
      setBrandDraft(
        createBrandIdentityDraft(savedBusiness.brandIdentity),
      );
      await updateBusiness(activeBusiness.id, {
        ...activeBusiness,
        ...form,
        brandIdentity: undefined,
      });
      setSaved(
        "Business profile and branding saved to the authoritative workspace.",
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "We couldn't save these settings.",
      );
    }
  };

  const resetBranding = async () => {
    setError("");
    setSaved("");
    try {
      const resetBusiness = await resetSettingsBranding(
        activeBusiness.id,
        updateBranding,
      );
      revokeBrandLogo(brandDraft.logo);
      brandingDraftIsDirty.current = false;
      setBrandDraft(createBrandIdentityDraft(resetBusiness.brandIdentity));
      setHasCustomBrand(false);
      setConfirmReset(false);
      setSaved(
        `Workspace colors reset to the ${PRODUCT_NAME} defaults. Your business logo was preserved.`,
      );
    } catch (reason) {
      setConfirmReset(false);
      setError(
        reason instanceof Error
          ? reason.message
          : "We couldn't reset the branding.",
      );
    }
  };

  const tabs = [
    "Business Profile",
    "Branding",
    "Products / Services",
    "Brand Voice",
    "Team",
    "AI Controls",
    "Ad Spend",
  ];

  return (
    <>
      <PageHeader
        eyebrow="Workspace administration"
        title="Settings"
        subtitle={`Shape how ${activeBusiness.name} and its AI team work together.`}
        action={
          <Button variant="primary" onClick={() => void save()}>
            <Save /> Save changes
          </Button>
        }
      />
      {saved && (
        <div className="ai-banner">
          <Check /> {saved}
        </div>
      )}
      {error && (
        <div className="settings-error" role="alert">
          <AlertCircle /> {error}
        </div>
      )}
      <div className="settings-layout">
        <div className="settings-nav">
          {tabs.map((item) => (
            <button
              className={tab === item ? "active" : ""}
              onClick={() => setTab(item)}
              key={item}
            >
              {item}
            </button>
          ))}
        </div>
        <Card className={tab === "Branding" ? "settings-branding-card" : ""}>
          <div className="tabs">
            <button className="tab active">{tab}</button>
          </div>

          {tab === "Business Profile" && (
            <div className="form-grid">
              <div className="field full">
                <label>Business name</label>
                <input
                  value={form.name}
                  onChange={(event) => change("name", event.target.value)}
                />
              </div>
              <div className="field">
                <label>Industry</label>
                <input value={form.industry} readOnly />
              </div>
              <div className="field">
                <label>Location</label>
                <input
                  value={form.location}
                  onChange={(event) => change("location", event.target.value)}
                />
              </div>
              <div className="field">
                <label>Timezone</label>
                <input
                  value={form.timezone}
                  onChange={(event) => change("timezone", event.target.value)}
                />
              </div>
              <div className="field">
                <label>Currency</label>
                <input
                  value={form.currency}
                  onChange={(event) => change("currency", event.target.value)}
                />
              </div>
              <div className="field full">
                <label>Website</label>
                <input
                  value={form.website}
                  onChange={(event) => change("website", event.target.value)}
                />
              </div>
            </div>
          )}

          {tab === "Branding" && (
            <div className="settings-branding">
              <div className="settings-branding-intro">
                <div>
                  <h2>Business branding</h2>
                  <p>
                    Personalize this workspace only. Colors are converted into
                    accessible semantic theme values.
                  </p>
                </div>
                <Badge tone={hasCustomBrand ? "success" : "neutral"}>
                  {hasCustomBrand ? "Custom theme" : `${PRODUCT_NAME} default`}
                </Badge>
              </div>
              <BrandingEditor
                businessName={form.name}
                value={brandDraft}
                onChange={(next) => {
                  brandingDraftIsDirty.current = true;
                  const colorsChanged =
                    next.primaryColor !== brandDraft.primaryColor ||
                    next.secondaryColor !== brandDraft.secondaryColor ||
                    next.accentColor !== brandDraft.accentColor;
                  setBrandDraft(next);
                  if (colorsChanged) setHasCustomBrand(true);
                  setSaved("");
                  setError("");
                }}
              />
              <div className="branding-settings-actions">
                <Button
                  variant="secondary"
                  onClick={() => setConfirmReset(true)}
                >
                  <RotateCcw /> Reset to {PRODUCT_NAME} defaults
                </Button>
                <Button variant="primary" onClick={() => void save()}>
                  <Save /> Save changes
                </Button>
              </div>
            </div>
          )}

          {tab === "Products / Services" && (
            <div className="empty">
              <ShieldCheck />
              <h3>Use the authoritative workspace</h3>
              <p>Products are managed in Catalog. Healthcare and professional services are managed as public appointment types in Scheduling. Settings never keeps a browser-only copy.</p>
              <div className="toolbar">
                {["Farm/Agriculture", "E-commerce", "Other"].includes(activeBusiness.industry) && <a className="btn btn-primary" href="/products">Open products</a>}
                {["Hospital", "Clinic", "Medical Practice", "Dental", "Professional Services"].includes(activeBusiness.industry) && <a className="btn btn-primary" href="/scheduling">Open services</a>}
                {activeBusiness.industry === "Real Estate" && <span className="subtle">Property listings remain intentionally hidden until the real Property domain exists.</span>}
              </div>
            </div>
          )}

          {tab === "Brand Voice" && (
            <div className="form-grid">
              <div className="field full">
                <label>Business description</label>
                <textarea
                  value={form.description}
                  onChange={(event) =>
                    change("description", event.target.value)
                  }
                />
              </div>
              <div className="field full">
                <label>Tone and personality</label>
                <textarea
                  value={form.tone}
                  onChange={(event) => change("tone", event.target.value)}
                />
              </div>
              <div className="field full">
                <label>Words to avoid</label>
                <input
                  value={form.avoidKeywords}
                  onChange={(event) =>
                    change("avoidKeywords", event.target.value)
                  }
                />
              </div>
              <div className="voice-preview">
                <Sparkles />
                <div>
                  <div className="eyebrow">Voice preview</div>
                  <strong>{form.tone}</strong>
                </div>
              </div>
            </div>
          )}

          {tab === "Team" && (
            <div className="list">
              <div className="list-row">
                <Avatar name="Current user" />
                <div className="row-main"><div className="row-title">Current authenticated membership</div><div className="row-copy">{activeBusiness.membershipRole}</div></div>
                <Badge>Server-authorized</Badge>
              </div>
              <p className="subtle">No sample teammates are shown. Invitation and role-management controls will appear only when their authenticated membership API is available.</p>
            </div>
          )}

          {tab === "AI Controls" && (
            <div>
              <div className="risk-note">
                <ShieldCheck />
                <div>
                  <strong>Autonomy is limited by risk</strong>
                  <p>
                    Autonomous mode applies only to approved low-risk action
                    classes. Publishing, money movement, credentials, refunds,
                    and irreversible changes still require human or backend
                    controls.
                  </p>
                </div>
              </div>
              <SectionTitle title="Agent autonomy & permissions" />
              <div className="risk-note">
                <Sparkles />
                <div>
                  <strong>Managed in the AI Workforce</strong>
                  <p>
                    Agent status, capabilities, and autonomy use the durable
                    business API. Integration credentials and connector write
                    controls are never exposed here.
                  </p>
                </div>
              </div>
              <div className="toolbar">
                <a className="btn btn-primary" href="/agents">
                  Manage AI agents
                </a>
                <a className="btn btn-secondary" href="/approvals">
                  Review approvals
                </a>
              </div>
            </div>
          )}

          {tab === "Ad Spend" && (
            !canManageSpend ? <div className="empty"><ShieldCheck /><h3>Owner or administrator access required</h3><p>Advertising authorization limits are intentionally hidden from members who cannot change spend governance.</p></div> : spendPolicy.isLoading ? <div className="empty"><RefreshCw className="spin" /><p>Loading server-owned spend limits…</p></div> : spendPolicy.isError ? <div className="empty"><AlertCircle /><h3>Spend policy could not load</h3><Button onClick={() => void spendPolicy.refetch()}>Retry</Button></div> : <form onSubmit={(event) => saveSpendPolicy.mutate(event)} key={spendPolicy.data?.updated_at ?? "new-policy"}>
              <div className="risk-note"><ShieldCheck /><div><strong>Human approval is not a spend limit</strong><p>These tenant-owned limits are revalidated from the server before durable advertising dispatch. AI and campaign forms cannot change them.</p></div></div>
              <div className="form-grid section-gap">
                <label className="field"><span>Policy currency</span><input value={activeBusiness.currency} readOnly /></label>
                <label className="field"><span>Maximum single campaign budget</span><input name="max_single_campaign_budget" type="number" min="0" max="1000000000" step="0.01" required defaultValue={spendPolicy.data?.max_single_campaign_budget ?? "0.00"} /></label>
                <label className="field"><span>Maximum single budget change</span><input name="max_single_budget_change" type="number" min="0" max="1000000000" step="0.01" required defaultValue={spendPolicy.data?.max_single_budget_change ?? "0.00"} /></label>
                <label className="field"><span>Optional daily advertising limit</span><input name="daily_advertising_limit" type="number" min="0" max="1000000000" step="0.01" defaultValue={spendPolicy.data?.daily_advertising_limit ?? ""} /></label>
                <label className="field"><span>Optional monthly AI-managed limit</span><input name="monthly_ai_managed_limit" type="number" min="0" max="1000000000" step="0.01" defaultValue={spendPolicy.data?.monthly_ai_managed_limit ?? ""} /></label>
                <label className="toggle-line full"><input name="active" type="checkbox" defaultChecked={spendPolicy.data?.active ?? true} /><span>Enforce this policy for advertising actions</span></label>
                <label className="toggle-line full"><input name="confirm_material_increase" type="checkbox" /><span>I explicitly confirm any increase to an advertising limit</span></label>
              </div>
              <div className="toolbar section-gap"><Button variant="primary" type="submit" disabled={saveSpendPolicy.isPending}><Save />{saveSpendPolicy.isPending ? "Saving limits…" : "Save governed limits"}</Button></div>
            </form>
          )}
        </Card>
      </div>

      {confirmReset && (
        <Modal
          title="Reset workspace colors?"
          description={`This will replace ${activeBusiness.name}'s custom colors with the ${PRODUCT_NAME} defaults. The saved business logo will remain.`}
          onClose={() => setConfirmReset(false)}
        >
          <div className="reset-branding-copy">
            <RotateCcw />
            <p>
              Only the workspace color theme will reset. The business logo,
              profile, brand voice, and other business data will not change.
            </p>
          </div>
          <div className="modal-foot">
            <Button onClick={() => setConfirmReset(false)}>
              Keep branding
            </Button>
            <Button variant="danger" onClick={() => void resetBranding()}>
              <RotateCcw /> Reset colors
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}

function hasSourceColors(business: Business | undefined): boolean {
  return hasCustomBrandColors(business?.brandIdentity);
}
