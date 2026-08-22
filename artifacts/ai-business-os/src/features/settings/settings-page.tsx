import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Check,
  Edit3,
  Plus,
  RotateCcw,
  Save,
  ShieldCheck,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useBusiness } from "@/business-context";
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
import { useWorkspaceData } from "@/hooks/use-workspace-data";
import {
  createBrandIdentityDraft,
  type BrandIdentityDraft,
} from "@/lib/brand-theme";
import {
  resetSettingsBranding,
  saveSettingsBranding,
} from "@/features/settings/settings-branding-save";
import { revokeBrandLogo } from "@/services/brand-logo";
import type { Business } from "@/types/business";

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
    deleteLogo,
    updateBranding,
    updateBusiness,
    uploadLogo,
  } = useBusiness();
  const { data, update } = useWorkspaceData();
  const [tab, setTab] = useState("Business Profile");
  const [saved, setSaved] = useState("");
  const [error, setError] = useState("");
  const [confirmReset, setConfirmReset] = useState(false);
  const [form, setForm] = useState(() =>
    activeBusiness ? createProfileForm(activeBusiness) : null,
  );
  const [brandDraft, setBrandDraft] = useState<BrandIdentityDraft>(() =>
    createBrandIdentityDraft(
      activeBusiness?.brandIdentity,
      activeBusiness?.theme === "navy" ? "navy" : "green",
    ),
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
      setBrandDraft(
        createBrandIdentityDraft(
          activeBusiness.brandIdentity,
          activeBusiness.theme === "navy" ? "navy" : "green",
        ),
      );
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

  const legacyTheme = activeBusiness.theme === "navy" ? "navy" : "green";
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
        createBrandIdentityDraft(savedBusiness.brandIdentity, legacyTheme),
      );
      await updateBusiness(activeBusiness.id, {
        ...activeBusiness,
        ...form,
        brandIdentity: undefined,
      });
      setSaved(
        "Branding saved. Other profile edits remain a browser draft until profile updates are available.",
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
      await resetSettingsBranding(
        activeBusiness.id,
        updateBranding,
        deleteLogo,
      );
      revokeBrandLogo(brandDraft.logo);
      brandingDraftIsDirty.current = false;
      setBrandDraft(createBrandIdentityDraft(undefined, legacyTheme));
      setHasCustomBrand(false);
      setConfirmReset(false);
      setSaved(
        "Branding reset to the AI Business OS defaults for this business.",
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
                  {hasCustomBrand ? "Custom theme" : "AI Business OS default"}
                </Badge>
              </div>
              <BrandingEditor
                businessName={form.name}
                value={brandDraft}
                legacyTheme={legacyTheme}
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
                  <RotateCcw /> Reset to AI Business OS defaults
                </Button>
                <Button variant="primary" onClick={() => void save()}>
                  <Save /> Save changes
                </Button>
              </div>
            </div>
          )}

          {tab === "Products / Services" && (
            <>
              <div className="list">
                {form.products.map((product, index) => (
                  <div
                    className="list-row product-settings-row"
                    key={product.id}
                  >
                    <div className="field row-main">
                      <label>Name</label>
                      <input
                        value={product.name}
                        onChange={(event) =>
                          change(
                            "products",
                            form.products.map((item, itemIndex) =>
                              itemIndex === index
                                ? { ...item, name: event.target.value }
                                : item,
                            ),
                          )
                        }
                      />
                    </div>
                    <div className="field">
                      <label>Price</label>
                      <input
                        type="number"
                        value={product.price}
                        onChange={(event) =>
                          change(
                            "products",
                            form.products.map((item, itemIndex) =>
                              itemIndex === index
                                ? { ...item, price: Number(event.target.value) }
                                : item,
                            ),
                          )
                        }
                      />
                    </div>
                    <div className="field">
                      <label>Availability</label>
                      <input
                        value={product.availability}
                        onChange={(event) =>
                          change(
                            "products",
                            form.products.map((item, itemIndex) =>
                              itemIndex === index
                                ? { ...item, availability: event.target.value }
                                : item,
                            ),
                          )
                        }
                      />
                    </div>
                    <Button
                      variant="danger"
                      className="btn-sm"
                      onClick={() =>
                        change(
                          "products",
                          form.products.filter(
                            (item) => item.id !== product.id,
                          ),
                        )
                      }
                    >
                      <Trash2 />
                    </Button>
                  </div>
                ))}
              </div>
              <Button
                variant="soft"
                className="btn-sm"
                onClick={() =>
                  change("products", [
                    ...form.products,
                    {
                      id: `product-${Date.now()}`,
                      name: "",
                      price: 0,
                      availability: "Available",
                    },
                  ])
                }
              >
                <Plus /> Add item
              </Button>
            </>
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
              {[
                [
                  "Current user",
                  activeBusiness.membershipRole,
                  "Authenticated access",
                ],
                ["Sam Rivera", "Manager", "Customers, operations, AI"],
                ["Tina Brooks", "Viewer", "Analytics only"],
              ].map((member) => (
                <div className="list-row" key={member[0]}>
                  <Avatar name={member[0]} />
                  <div className="row-main">
                    <div className="row-title">{member[0]}</div>
                    <div className="row-copy">{member[1]}</div>
                  </div>
                  <Badge>{member[2]}</Badge>
                  <Button
                    className="btn-sm"
                    onClick={() =>
                      setSaved(
                        `Team permissions for ${member[0]} opened in prototype mode.`,
                      )
                    }
                  >
                    <Edit3 />
                  </Button>
                </div>
              ))}
              <Button
                variant="soft"
                className="btn-sm"
                onClick={() =>
                  setSaved(
                    "Invitation prepared. Email delivery requires the secure backend.",
                  )
                }
              >
                <Plus /> Invite teammate
              </Button>
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
              <SectionTitle title="Workspace approval rules" />
              <div className="toggle-row">
                <div className="toggle-copy">
                  <strong>Proactive actions</strong>
                  <span>
                    Let AI prepare timely actions from clear business signals.
                  </span>
                </div>
                <input type="checkbox" defaultChecked />
              </div>
              <div className="toggle-row">
                <div className="toggle-copy">
                  <strong>Require approval for external messages</strong>
                  <span>
                    Human review remains visible before promotional outreach.
                  </span>
                </div>
                <input type="checkbox" defaultChecked />
              </div>
              <SectionTitle title="Agent autonomy & permissions" />
              {data.agents.map((agent) => (
                <div className="agent-control-row" key={agent.id}>
                  <div>
                    <strong>{agent.name}</strong>
                    <span>{agent.role}</span>
                  </div>
                  <select
                    value={agent.autonomy}
                    onChange={(event) =>
                      update((current) => ({
                        ...current,
                        agents: current.agents.map((item) =>
                          item.id === agent.id
                            ? {
                                ...item,
                                autonomy: event.target
                                  .value as typeof item.autonomy,
                              }
                            : item,
                        ),
                      }))
                    }
                  >
                    <option>Suggest</option>
                    <option>Approval</option>
                    <option>Autonomous</option>
                  </select>
                  <Badge tone={agent.active ? "success" : "neutral"}>
                    {agent.active ? "Active" : "Paused"}
                  </Badge>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {confirmReset && (
        <Modal
          title="Reset business branding?"
          description={`This will remove ${activeBusiness.name}'s custom colors and saved logo.`}
          onClose={() => setConfirmReset(false)}
        >
          <div className="reset-branding-copy">
            <RotateCcw />
            <p>
              The workspace will return to the polished AI Business OS default
              theme. Other business settings and data will not change.
            </p>
          </div>
          <div className="modal-foot">
            <Button onClick={() => setConfirmReset(false)}>
              Keep branding
            </Button>
            <Button variant="danger" onClick={() => void resetBranding()}>
              <RotateCcw /> Reset branding
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}

function hasSourceColors(business: Business | undefined): boolean {
  const identity = business?.brandIdentity;
  return Boolean(
    identity?.primaryColor || identity?.secondaryColor || identity?.accentColor,
  );
}
