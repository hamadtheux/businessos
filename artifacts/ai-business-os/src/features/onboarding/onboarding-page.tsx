import { useEffect, useRef, useState } from "react";
import type { BusinessInput } from "@/types/business";
import { useLocation } from "wouter";
import {
  AlertCircle,
  Archive,
  ArrowRight,
  Bot,
  Check,
  Globe2,
  Link2,
  Mail,
  MessageCircle,
  RefreshCw,
} from "lucide-react";
import { useBusiness } from "@/business-context";
import { Badge, Button, Card } from "@/components/product-ui";
import {
  BrandingEditor,
  brandingDraftHasErrors,
} from "@/features/branding/branding-editor";
import {
  brandIdentityFromDraft,
  createBrandIdentityDraft,
  type BrandIdentityDraft,
} from "@/lib/brand-theme";
import {
  businessIndustryDefaultTheme,
  ONBOARDING_INDUSTRIES,
} from "@/lib/business-industries";
import { cx } from "@/lib/product-utils";
import { isWorkspaceModuleVisible } from "@/lib/industry-workspaces";
import { revokeBrandLogo } from "@/services/brand-logo";
import { BusinessLogoUploadAfterCreationError } from "@/services/business-onboarding-persistence";
import { catalogApi } from "@/services/catalog";
import {
  catalogDraftForSessionStorage,
  createInitialCatalogDraft,
  restoreCatalogDraft,
  type CatalogDraft,
} from "./catalog-import";
import {
  CatalogPersistenceAfterBusinessCreationError,
  createOnboardingBusinessId,
  humanizeOnboardingSaveError,
  onboardingSetupSteps,
  persistOnboardingCatalog,
  saveOnboardingWorkspace,
} from "./onboarding-save";
import { ProductCatalogStep } from "./product-catalog-step";

const onboardingChannels = [
  {
    name: "WhatsApp",
    description: "Customer conversations and order updates",
    icon: MessageCircle,
  },
  {
    name: "Instagram",
    description: "Content publishing and engagement",
    icon: Globe2,
  },
  { name: "Email", description: "Inbox and customer updates", icon: Mail },
  {
    name: "Stripe",
    description: "Payments and transaction status",
    icon: Archive,
  },
] as const;

type SetupState = "idle" | "saving" | "success" | "failed";

const ONBOARDING_DRAFT_KEY = "ai-business-os:onboarding-draft:v2";

export function OnboardingPage() {
  const [, setLocation] = useLocation();
  const { createBusiness, selectBusiness } = useBusiness();
  const [step, setStep] = useState(0);
  const [setupState, setSetupState] = useState<SetupState>("idle");
  const [completedSetupSteps, setCompletedSetupSteps] = useState(0);
  const [setupError, setSetupError] = useState("");
  const [createdBusinessId, setCreatedBusinessId] = useState("");
  const [businessSavedBeforeFailure, setBusinessSavedBeforeFailure] =
    useState(false);
  const [catalogSaveFailed, setCatalogSaveFailed] = useState(false);
  const [businessId, setBusinessId] = useState(createOnboardingBusinessId);
  const saveInFlight = useRef(false);
  const [notice, setNotice] = useState("");
  const [draftReady, setDraftReady] = useState(false);
  const [catalog, setCatalog] = useState<CatalogDraft>(() =>
    createInitialCatalogDraft(),
  );
  const [catalogFile, setCatalogFile] = useState<File | null>(null);
  const [brandDraft, setBrandDraft] = useState<BrandIdentityDraft>(() =>
    createBrandIdentityDraft(),
  );
  const pendingLogo = useRef(brandDraft.logo);
  pendingLogo.current = brandDraft.logo;
  const [brandingSkipped, setBrandingSkipped] = useState(false);
  const [brandingTouched, setBrandingTouched] = useState(false);
  const [form, setForm] = useState({
    name: "",
    industry: "Farm/Agriculture" as BusinessInput["industry"],
    website: "",
    location: "",
    timezone: "Asia/Karachi",
    currency: "USD",
    locale: "en",
    description: "",
    tone: "Warm, grounded, and useful",
    avoidKeywords: "",
    channels: [] as NonNullable<BusinessInput["connectedChannels"]>,
  });

  const catalogEnabled = isWorkspaceModuleVisible(form.industry, "catalog");

  useEffect(() => {
    if (step === 1 && !catalogEnabled) {
      setNotice("");
      setStep(2);
    }
  }, [catalogEnabled, step]);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(ONBOARDING_DRAFT_KEY);
      if (raw) {
        const draft = JSON.parse(raw) as {
          step?: number;
          form?: typeof form;
          catalog?: Partial<CatalogDraft>;
          businessId?: string;
          brandDraft?: BrandIdentityDraft;
          brandingSkipped?: boolean;
          brandingTouched?: boolean;
        };
        if (draft.businessId) setBusinessId(draft.businessId);
        if (draft.form) setForm(draft.form);
        if (draft.catalog) {
          setCatalog(restoreCatalogDraft(draft.catalog));
        } else {
          const legacyProducts = (
            draft.form as
              | (typeof form & { products?: CatalogDraft["products"] })
              | undefined
          )?.products;
          if (legacyProducts?.length) {
            setCatalog(
              restoreCatalogDraft({
                method: "manual",
                products: legacyProducts,
              }),
            );
          }
        }
        if (draft.brandDraft) {
          setBrandDraft(onboardingBrandDraftFromStorage(draft.brandDraft));
        }
        setBrandingSkipped(Boolean(draft.brandingSkipped));
        setBrandingTouched(Boolean(draft.brandingTouched));
        if (typeof draft.step === "number") {
          setStep(Math.min(4, Math.max(0, draft.step)));
        }
      }
    } catch {
      // Start clean when a stale UI draft cannot be read.
    }
    setDraftReady(true);
  }, []);

  useEffect(
    () => () => {
      revokeBrandLogo(pendingLogo.current);
    },
    [],
  );

  useEffect(() => {
    if (!draftReady || setupState === "success") return;
    try {
      sessionStorage.setItem(
        ONBOARDING_DRAFT_KEY,
        JSON.stringify({
          step: Math.min(step, 4),
          form,
          catalog: catalogDraftForSessionStorage(catalog),
          brandDraft: onboardingBrandDraftForStorage(brandDraft),
          brandingSkipped,
          brandingTouched,
          businessId,
        }),
      );
    } catch {
      // Persistence errors are surfaced by the final save operation.
    }
  }, [
    brandDraft,
    brandingSkipped,
    brandingTouched,
    catalog,
    businessId,
    draftReady,
    form,
    setupState,
    step,
  ]);

  useEffect(() => {
    if (step !== 5 || setupState !== "saving" || saveInFlight.current) return;
    saveInFlight.current = true;

    const save = async () => {
      try {
        const business = await saveOnboardingWorkspace(
          {
            ...form,
            brandIdentity: brandingSkipped
              ? undefined
              : brandIdentityFromDraft(brandDraft),
          },
          catalog,
          businessId,
          {
            createBusiness,
            saveCatalog: async (created, currentCatalog) => {
              await persistOnboardingCatalog(
                catalogApi,
                created.id,
                currentCatalog,
                catalogFile,
              );
            },
            onProgress: async (completed) => {
              if (completed > 0) {
                await new Promise((resolve) => window.setTimeout(resolve, 180));
              }
              setCompletedSetupSteps(completed);
            },
          },
        );
        revokeBrandLogo(brandDraft.logo);
        setBrandDraft(
          createBrandIdentityDraft(
            business.brandIdentity,
            businessIndustryDefaultTheme(business.industry),
          ),
        );
        setCreatedBusinessId(business.id);
        setBusinessSavedBeforeFailure(false);
        setCatalogSaveFailed(false);
        setCatalogFile(null);
        setSetupError("");
        setSetupState("success");
      } catch (error) {
        const catalogWasSaved =
          error instanceof CatalogPersistenceAfterBusinessCreationError;
        const businessWasSaved =
          error instanceof BusinessLogoUploadAfterCreationError ||
          catalogWasSaved;
        setBusinessSavedBeforeFailure(businessWasSaved);
        setCatalogSaveFailed(catalogWasSaved);
        if (businessWasSaved) setCreatedBusinessId(businessId);
        setSetupError(humanizeOnboardingSaveError(error));
        setSetupState("failed");
      } finally {
        saveInFlight.current = false;
      }
    };

    void save();
  }, [
    brandDraft,
    brandingSkipped,
    catalog,
    catalogFile,
    createBusiness,
    businessId,
    form,
    setupState,
    step,
  ]);

  const update = <K extends keyof typeof form>(
    key: K,
    value: (typeof form)[K],
  ) => {
    setForm((current) => ({ ...current, [key]: value }));
    if (key === "industry" && !brandingTouched) {
      setBrandDraft(
        createBrandIdentityDraft(
          undefined,
          value === "Real Estate" ? "navy" : "green",
        ),
      );
    }
  };

  const toggleChannel = (
    channel: (typeof onboardingChannels)[number]["name"],
  ) => {
    const channels = form.channels.includes(channel)
      ? form.channels.filter((item) => item !== channel)
      : [...form.channels, channel];
    update("channels", channels);
  };

  const canContinue =
    step === 0
      ? Boolean(form.name.trim() && form.industry)
      : step === 1
        ? catalog.method === "skip" ||
          (catalog.method === "manual"
            ? catalog.products.some((product) => product.name.trim())
            : catalog.method !== "store" &&
              Boolean(catalog.method && catalog.confirmed && catalogFile))
        : step === 4
          ? !brandingDraftHasErrors(brandDraft)
          : true;

  const beginSetup = (skipBranding = false) => {
    setCompletedSetupSteps(0);
    setSetupError("");
    setCreatedBusinessId("");
    setBusinessSavedBeforeFailure(false);
    setCatalogSaveFailed(false);
    setBrandingSkipped(skipBranding);
    setSetupState("saving");
    setStep(5);
  };

  const next = () => {
    if (!canContinue) {
      setNotice(
        step === 0
          ? "Add a business name and choose an industry to continue."
          : step === 4
            ? "Check the HEX values before continuing."
            : !catalog.method
              ? "Choose how you want to add products, or skip this step for now."
              : catalog.method === "store"
                ? "Store provider configuration is required. Choose another option or skip for now."
                : catalog.method === "manual"
                  ? "Add at least one product or service to continue."
                  : "Review and confirm this catalog option before continuing.",
      );
      return;
    }
    setNotice("");
    if (step === 4) {
      beginSetup(false);
      return;
    }
    if (step === 0 && !catalogEnabled) {
      setStep(2);
      return;
    }
    setStep((current) => Math.min(4, current + 1));
  };

  const retrySetup = () => {
    if (saveInFlight.current) return;
    setCompletedSetupSteps(0);
    setSetupError("");
    setSetupState("saving");
  };

  const backToReview = () => {
    if (saveInFlight.current) return;
    setSetupError("");
    setSetupState("idle");
    setBusinessSavedBeforeFailure(false);
    setCatalogSaveFailed(false);
    setStep(4);
  };

  const backToCatalog = () => {
    if (saveInFlight.current) return;
    setSetupError("");
    setSetupState("idle");
    setBusinessSavedBeforeFailure(false);
    setCatalogSaveFailed(false);
    setStep(1);
  };

  const skipFailedLogo = () => {
    if (saveInFlight.current) return;
    revokeBrandLogo(brandDraft.logo);
    setBrandDraft((current) => ({ ...current, logo: undefined }));
    setCompletedSetupSteps(0);
    setSetupError("");
    setSetupState("saving");
  };

  const skipFailedCatalog = () => {
    if (saveInFlight.current) return;
    setCatalogFile(null);
    setCatalog((current) => ({
      ...current,
      method: "skip",
      confirmed: true,
      sourceName: "Skipped after catalog save failure",
      pastedText: "",
      products: [],
    }));
    setCompletedSetupSteps(0);
    setSetupError("");
    setCatalogSaveFailed(false);
    setSetupState("saving");
  };

  const openDashboard = () => {
    if (!createdBusinessId || setupState !== "success") return;
    selectBusiness(createdBusinessId);
    sessionStorage.removeItem(ONBOARDING_DRAFT_KEY);
    setLocation(form.channels.length ? "/integrations" : "/dashboard");
  };

  const stepTitles: Record<number, string> = {
    0: "Business basics",
    1: "Products & services",
    2: "Brand voice",
    3: "Plan connections",
    4: "Brand identity",
    5: "AI team setup",
  };

  const visibleStepIndexes = catalogEnabled
    ? [0, 1, 2, 3, 4, 5]
    : [0, 2, 3, 4, 5];

  const activeVisibleStepIndex = visibleStepIndexes.indexOf(step);
  const visibleProgressIndex = Math.max(activeVisibleStepIndex, 0);

  const setupProgress = Math.round(
    (completedSetupSteps / onboardingSetupSteps.length) * 100,
  );
  const pageTitle =
    step !== 5
      ? stepTitles[step]
      : setupState === "success"
        ? "Your workspace is ready"
        : setupState === "failed"
          ? businessSavedBeforeFailure
            ? catalogSaveFailed
              ? "Your business is saved — the catalog needs another try"
              : "Your business is saved — the logo needs another try"
            : "We couldn't finish setting up your workspace"
          : "Setting up your AI Business Team...";
  const pageDescription =
    step === 0
      ? "Tell us a little about your business so your AI team can make better decisions from day one."
      : step === 1
        ? "Give your team the products, services, prices, and details it should know about."
        : step === 2
          ? "Your AI team will use this voice whenever it writes, replies, or recommends."
          : step === 3
            ? "Choose which real provider connections you want to configure after the workspace exists. Nothing connects on this step."
            : step === 4
              ? "Add your brand identity and we'll personalize your workspace while keeping everything clear, accessible, and professional."
              : setupState === "success"
                ? "Your core workspace is saved. The live readiness checklist will show what still needs configuration."
                : setupState === "failed"
                  ? businessSavedBeforeFailure
                    ? catalogSaveFailed
                      ? "The workspace already exists. Retry the same catalog against this business, or skip it without creating another business."
                      : "The workspace already exists. Retry only the logo upload, or skip the logo without creating another business."
                    : "Your onboarding information is still safe. Nothing has been lost."
                  : "We are saving your workspace and preparing a focused team around the way your business works.";

  return (
    <div className="onboarding-screen">
      <div className="onboarding-top">
        <div className="brand onboarding-brand">
          <div className="brand-mark">AI</div>
          <div>
            <div className="brand-copy">AI Business OS</div>
            <div className="brand-sub">build your business command room</div>
          </div>
        </div>
        <div className="onboarding-progress">
          {visibleStepIndexes.map((stepIndex, visibleIndex) => {
            const title = stepTitles[stepIndex];
            const complete = visibleIndex < activeVisibleStepIndex;

            return (
              <div
                className={cx(
                  "onboarding-step",
                  stepIndex === step && "active",
                  complete && "complete",
                )}
                key={stepIndex}
              >
                <span>
                  {complete ? <Check size={13} /> : visibleIndex + 1}
                </span>
                <small>{title}</small>
              </div>
            );
          })}
        </div>
        <div className="onboarding-help">
          Step {visibleProgressIndex + 1} of {visibleStepIndexes.length}
        </div>
      </div>
      <div className="onboarding-progress-line">
        <i
          style={{
            width: `${
              ((visibleProgressIndex +
                (step === 5 ? setupProgress / 100 : 0)) /
                visibleStepIndexes.length) *
              100
            }%`,
          }}
        />
      </div>
      <main className="onboarding-body">
        <div className="onboarding-copy">
          <div className="eyebrow">Welcome to your command room</div>
          <h1>{pageTitle}</h1>
          <p>{pageDescription}</p>
        </div>
        <Card
          className={cx(
            "onboarding-card",
            step === 4 && "branding-onboarding-card",
          )}
          pad={false}
        >
          {step === 0 && (
            <div className="onboarding-panel">
              <div className="form-grid">
                <div className="field full">
                  <label>Business name</label>
                  <input
                    autoFocus
                    value={form.name}
                    onChange={(event) => update("name", event.target.value)}
                    placeholder="e.g. Green Valley Farms"
                    data-testid="input-onboarding-business-name"
                  />
                </div>
                <div className="field">
                  <label>Industry</label>
                  <select
                    value={form.industry}
                    onChange={(event) =>
                      update(
                        "industry",
                        event.target.value as BusinessInput["industry"],
                      )
                    }
                    data-testid="select-onboarding-industry"
                  >
                    {ONBOARDING_INDUSTRIES.map((industry) => (
                      <option key={industry}>{industry}</option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label>Website</label>
                  <input
                    value={form.website}
                    onChange={(event) => update("website", event.target.value)}
                    placeholder="yourbusiness.com"
                    data-testid="input-onboarding-website"
                  />
                </div>
                <div className="field">
                  <label>Location</label>
                  <input
                    value={form.location}
                    onChange={(event) => update("location", event.target.value)}
                    placeholder="City, country"
                    data-testid="input-onboarding-location"
                  />
                </div>
                <div className="field">
                  <label>Timezone</label>
                  <select
                    value={form.timezone}
                    onChange={(event) => update("timezone", event.target.value)}
                    data-testid="select-onboarding-timezone"
                  >
                    <option>Asia/Karachi</option>
                    <option>America/Los_Angeles</option>
                    <option>America/Chicago</option>
                    <option>America/New_York</option>
                    <option>Europe/London</option>
                    <option>UTC</option>
                  </select>
                </div>
                <div className="field">
                  <label>Currency</label>
                  <select
                    value={form.currency}
                    onChange={(event) => update("currency", event.target.value)}
                    data-testid="select-onboarding-currency"
                  >
                    <option value="USD">USD · $</option>
                    <option value="PKR">PKR · ₨</option>
                    <option value="EUR">EUR · €</option>
                    <option value="GBP">GBP · £</option>
                  </select>
                </div>
              </div>
            </div>
          )}

          {step === 1 && catalogEnabled && (
            <ProductCatalogStep
              catalog={catalog}
              selectedFile={catalogFile}
              onChange={setCatalog}
              onFileChange={setCatalogFile}
            />
          )}

          {step === 2 && (
            <div className="onboarding-panel">
              <div className="field">
                <label>Business description</label>
                <textarea
                  autoFocus
                  value={form.description}
                  onChange={(event) =>
                    update("description", event.target.value)
                  }
                  placeholder="What does your business do, and who does it serve?"
                  data-testid="textarea-onboarding-description"
                />
              </div>
              <div className="form-grid onboarding-form-gap">
                <div className="field">
                  <label>Tone of voice</label>
                  <input
                    value={form.tone}
                    onChange={(event) => update("tone", event.target.value)}
                    placeholder="Warm, clear, confident..."
                    data-testid="input-onboarding-tone"
                  />
                </div>
                <div className="field">
                  <label>Keywords to avoid</label>
                  <input
                    value={form.avoidKeywords}
                    onChange={(event) =>
                      update("avoidKeywords", event.target.value)
                    }
                    placeholder="Words or phrases, comma separated"
                    data-testid="input-onboarding-avoid-keywords"
                  />
                </div>
              </div>
              <div className="voice-preview">
                <div className="voice-preview-mark">
                  <Bot size={17} />
                </div>
                <div>
                  <div className="eyebrow">Voice preview</div>
                  <div className="row-title">
                    Your AI team will sound like a thoughtful partner, not a
                    generic chatbot.
                  </div>
                </div>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="onboarding-panel">
              <div className="channel-grid onboarding-channel-grid">
                {onboardingChannels.map(({ name, description, icon: Icon }) => {
                  const selected = form.channels.includes(name);
                  return (
                    <button
                      key={name}
                      className={cx(
                        "onboarding-channel-card",
                        selected && "connected",
                      )}
                      onClick={() => toggleChannel(name)}
                      data-testid={`button-onboarding-channel-${name.toLowerCase()}`}
                    >
                      <div className="integration-icon">
                        <Icon />
                      </div>
                      <div className="row-main">
                        <div className="row-title">{name}</div>
                        <div className="row-copy">{description}</div>
                      </div>
                      <span className={cx("channel-toggle", selected && "on")}>
                        <i />
                        {selected ? "In setup plan" : "Add to plan"}
                      </span>
                    </button>
                  );
                })}
              </div>
              <div className="onboarding-tip">
                <Link2 size={16} />
                <span>
                  This records setup intent only. OAuth, account selection, and
                  health verification happen in Integrations after creation.
                </span>
              </div>
            </div>
          )}

          {step === 4 && (
            <div className="onboarding-panel branding-onboarding-panel">
              <BrandingEditor
                businessName={form.name}
                value={brandDraft}
                legacyTheme={businessIndustryDefaultTheme(form.industry)}
                onChange={(next) => {
                  setBrandDraft(next);
                  setBrandingSkipped(false);
                  setBrandingTouched(true);
                }}
              />
            </div>
          )}

          {step === 5 && (
            <div className="onboarding-panel onboarding-team-panel">
              {setupState === "saving" && (
                <>
                  <div className="team-progress-head">
                    <span>Setting up your AI Business Team</span>
                    <strong>{setupProgress}%</strong>
                  </div>
                  <div className="team-progress">
                    <i style={{ width: `${setupProgress}%` }} />
                  </div>
                  <div className="ai-team-list">
                    {onboardingSetupSteps.map((name, index) => {
                      const ready = index < completedSetupSteps;
                      const active = index === completedSetupSteps;
                      return (
                        <div
                          className={cx(
                            "ai-team-row",
                            ready && "ready",
                            active && "active",
                          )}
                          key={name}
                        >
                          <div className="agent-icon">
                            {ready ? (
                              <Check />
                            ) : active ? (
                              <RefreshCw className="spin" />
                            ) : (
                              <span className="setup-step-dot" />
                            )}
                          </div>
                          <div className="row-main">
                            <div className="row-title">{name}</div>
                            <div className="row-copy">
                              {ready
                                ? "Complete"
                                : active
                                  ? "In progress"
                                  : "Waiting"}
                            </div>
                          </div>
                          {ready && <Badge tone="success">Done</Badge>}
                        </div>
                      );
                    })}
                  </div>
                </>
              )}

              {setupState === "success" && (
                <div className="onboarding-success">
                  <div className="success-mark">
                    <Check />
                  </div>
                  <div className="eyebrow">Core setup complete</div>
                  <h2>Your workspace is saved</h2>
                  <p>
                    Business facts, catalog choices, and brand settings for{" "}
                    {form.name || "your business"} are saved. Provider
                    connections and AI autonomy are verified separately.
                  </p>
                </div>
              )}

              {setupState === "failed" && (
                <div className="onboarding-failure">
                  <div className="failure-mark">
                    <AlertCircle />
                  </div>
                  <div className="eyebrow">Setup paused</div>
                  <h2>
                    {businessSavedBeforeFailure
                      ? catalogSaveFailed
                        ? "Business saved. Catalog save paused."
                        : "Business saved. Logo upload paused."
                      : "We couldn't finish setting up your workspace"}
                  </h2>
                  <p>
                    {businessSavedBeforeFailure
                      ? catalogSaveFailed
                        ? "Your business is safe and has not been duplicated. Retry the catalog, fix the selected data, or skip it for now."
                        : "Your business and colors are safe. Continue by retrying the selected logo or skipping it for now."
                      : "Your onboarding information is still safe. Nothing has been lost."}
                  </p>
                  {setupError && (
                    <div
                      className="onboarding-failure-message"
                      role="alert"
                      data-testid="onboarding-save-error"
                    >
                      <AlertCircle size={15} />
                      <span>{setupError}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {notice && step < 5 && (
            <div className="onboarding-notice">
              <AlertCircle size={15} />
              {notice}
            </div>
          )}
          <div className="onboarding-actions">
            {step > 0 && step < 5 && (
              <Button
                variant="secondary"
                onClick={() => {
                  setNotice("");
                  setStep((current) =>
                    !catalogEnabled && current === 2 ? 0 : current - 1,
                  );
                }}
                data-testid="button-onboarding-back"
              >
                Back
              </Button>
            )}
            {step === 5 && setupState === "saving" && (
              <Button
                variant="primary"
                disabled
                data-testid="button-onboarding-saving"
              >
                <RefreshCw className="spin" /> Setting up…
              </Button>
            )}
            {step === 5 && setupState === "failed" && (
              <>
                {catalogSaveFailed && (
                  <Button
                    variant="secondary"
                    onClick={backToCatalog}
                    data-testid="button-onboarding-back-to-catalog"
                  >
                    Fix catalog
                  </Button>
                )}
                {businessSavedBeforeFailure ? (
                  <Button
                    variant="secondary"
                    onClick={
                      catalogSaveFailed ? skipFailedCatalog : skipFailedLogo
                    }
                    data-testid={
                      catalogSaveFailed
                        ? "button-onboarding-skip-failed-catalog"
                        : "button-onboarding-skip-failed-logo"
                    }
                  >
                    {catalogSaveFailed
                      ? "Skip catalog for now"
                      : "Skip logo for now"}
                  </Button>
                ) : (
                  <Button
                    variant="secondary"
                    onClick={backToReview}
                    data-testid="button-onboarding-back-to-review"
                  >
                    Back to review
                  </Button>
                )}
                <Button
                  variant="primary"
                  onClick={retrySetup}
                  data-testid="button-onboarding-retry"
                >
                  {businessSavedBeforeFailure
                    ? catalogSaveFailed
                      ? "Retry catalog"
                      : "Retry logo"
                    : "Try again"}{" "}
                  <RefreshCw />
                </Button>
              </>
            )}
            {step === 5 && setupState === "success" && (
              <Button
                variant="primary"
                onClick={openDashboard}
                data-testid="button-finish-onboarding"
              >
                {form.channels.length ? "Configure real connections" : "Open readiness dashboard"} <ArrowRight />
              </Button>
            )}
            {step === 4 && (
              <Button
                variant="secondary"
                onClick={() => beginSetup(true)}
                data-testid="button-onboarding-skip-branding"
              >
                Skip for now
              </Button>
            )}
            {step < 5 && (
              <Button
                variant="green"
                onClick={next}
                disabled={(step === 1 || step === 4) && !canContinue}
                data-testid="button-onboarding-next"
              >
                {step === 4 ? "Build my AI team" : "Continue"} <ArrowRight />
              </Button>
            )}
          </div>
        </Card>
      </main>
    </div>
  );
}

function onboardingBrandDraftForStorage(
  draft: BrandIdentityDraft,
): Omit<BrandIdentityDraft, "logo" | "logoUrl"> {
  return {
    primaryColor: draft.primaryColor,
    secondaryColor: draft.secondaryColor,
    accentColor: draft.accentColor,
  };
}

function onboardingBrandDraftFromStorage(
  draft: BrandIdentityDraft,
): BrandIdentityDraft {
  return {
    primaryColor: draft.primaryColor,
    secondaryColor: draft.secondaryColor,
    accentColor: draft.accentColor,
  };
}
