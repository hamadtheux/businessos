import { useEffect, useRef, useState } from "react";
import type { BusinessInput } from "@workspace/api-client-react";
import { useLocation } from "wouter";
import {
  AlertCircle,
  Archive,
  ArrowRight,
  Bot,
  Check,
  Globe2,
  Lightbulb,
  Link2,
  Mail,
  MessageCircle,
  RefreshCw,
} from "lucide-react";
import { useBusiness } from "@/business-context";
import { Badge, Button, Card } from "@/components/product-ui";
import { cx } from "@/lib/product-utils";
import { workspaceRepository } from "@/services/workspace-repository";
import { createInitialCatalogDraft, type CatalogDraft } from "./catalog-import";
import {
  addCatalogToWorkspace,
  createOnboardingBusinessId,
  humanizeOnboardingSaveError,
  onboardingSetupSteps,
  saveOnboardingWorkspace,
} from "./onboarding-save";
import { ProductCatalogStep } from "./product-catalog-step";

const onboardingIndustries = [
  "Farm/Agriculture",
  "Real Estate",
  "E-commerce",
  "Dental",
  "Other",
] as const;

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

function createDraftId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function OnboardingPage() {
  const [, setLocation] = useLocation();
  const { createBusiness, selectBusiness } = useBusiness();
  const [step, setStep] = useState(0);
  const [setupState, setSetupState] = useState<SetupState>("idle");
  const [completedSetupSteps, setCompletedSetupSteps] = useState(0);
  const [setupError, setSetupError] = useState("");
  const [createdBusinessId, setCreatedBusinessId] = useState("");
  const [draftId, setDraftId] = useState(createDraftId);
  const saveInFlight = useRef(false);
  const [notice, setNotice] = useState("");
  const [draftReady, setDraftReady] = useState(false);
  const [catalog, setCatalog] = useState<CatalogDraft>(() =>
    createInitialCatalogDraft(),
  );
  const [form, setForm] = useState({
    name: "",
    industry: "Farm/Agriculture" as BusinessInput["industry"],
    website: "",
    location: "",
    timezone: "Asia/Karachi",
    currency: "USD · $",
    description: "",
    tone: "Warm, grounded, and useful",
    avoidKeywords: "",
    channels: [] as NonNullable<BusinessInput["connectedChannels"]>,
  });

  useEffect(() => {
    try {
      if (sessionStorage.getItem("ai-business-os:new-registration")) {
        sessionStorage.removeItem("ai-business-os:onboarding-draft");
        sessionStorage.removeItem("ai-business-os:new-registration");
      } else {
        const raw = sessionStorage.getItem("ai-business-os:onboarding-draft");
        if (raw) {
          const draft = JSON.parse(raw) as {
            step?: number;
            form?: typeof form;
            catalog?: CatalogDraft;
            draftId?: string;
          };
          if (draft.draftId) setDraftId(draft.draftId);
          if (draft.form) setForm(draft.form);
          if (draft.catalog) {
            setCatalog({
              ...createInitialCatalogDraft(),
              ...draft.catalog,
              products:
                draft.catalog.products ?? createInitialCatalogDraft().products,
            });
          } else {
            const legacyProducts = (
              draft.form as
                | (typeof form & { products?: CatalogDraft["products"] })
                | undefined
            )?.products;
            if (legacyProducts?.length) {
              setCatalog({
                ...createInitialCatalogDraft(),
                method: "manual",
                products: legacyProducts.map((product) => ({
                  ...createInitialCatalogDraft().products[0],
                  ...product,
                })),
              });
            }
          }
          if (typeof draft.step === "number") {
            setStep(Math.min(3, Math.max(0, draft.step)));
          }
        }
      }
    } catch {
      // Start clean when a stale prototype draft cannot be read.
    }
    setDraftReady(true);
  }, []);

  useEffect(() => {
    if (!draftReady || setupState === "success") return;
    try {
      sessionStorage.setItem(
        "ai-business-os:onboarding-draft",
        JSON.stringify({ step: Math.min(step, 3), form, catalog, draftId }),
      );
    } catch {
      // Persistence errors are surfaced by the final save operation.
    }
  }, [catalog, draftId, draftReady, form, setupState, step]);

  useEffect(() => {
    if (step !== 4 || setupState !== "saving" || saveInFlight.current) return;
    saveInFlight.current = true;
    const businessId = createOnboardingBusinessId(form.name, draftId);

    const save = async () => {
      try {
        const business = await saveOnboardingWorkspace(
          form,
          catalog,
          businessId,
          {
            createBusiness,
            saveWorkspace: (created, products, currentCatalog) => {
              workspaceRepository.updateOrThrow(
                created.id,
                created.industry,
                (current) =>
                  addCatalogToWorkspace(current, currentCatalog, products),
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
        setCreatedBusinessId(business.id);
        setSetupError("");
        setSetupState("success");
      } catch (error) {
        setSetupError(humanizeOnboardingSaveError(error));
        setSetupState("failed");
      } finally {
        saveInFlight.current = false;
      }
    };

    void save();
  }, [catalog, createBusiness, draftId, form, setupState, step]);

  const update = <K extends keyof typeof form>(
    key: K,
    value: (typeof form)[K],
  ) => {
    setForm((current) => ({ ...current, [key]: value }));
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
            : Boolean(catalog.method && catalog.confirmed))
        : true;

  const next = () => {
    if (!canContinue) {
      setNotice(
        step === 0
          ? "Add a business name and choose an industry to continue."
          : !catalog.method
            ? "Choose how you want to add products, or skip this step for now."
            : catalog.method === "manual"
              ? "Add at least one product or service to continue."
              : "Review and confirm this catalog option before continuing.",
      );
      return;
    }
    setNotice("");
    if (step === 3) {
      setCompletedSetupSteps(0);
      setSetupError("");
      setCreatedBusinessId("");
      setSetupState("saving");
      setStep(4);
      return;
    }
    setStep((current) => Math.min(3, current + 1));
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
    setStep(3);
  };

  const openDashboard = () => {
    if (!createdBusinessId || setupState !== "success") return;
    selectBusiness(createdBusinessId);
    sessionStorage.removeItem("ai-business-os:onboarding-draft");
    sessionStorage.removeItem("ai-business-os:new-registration");
    setLocation("/dashboard");
  };

  const stepTitles = [
    "Business basics",
    "Products & services",
    "Brand voice",
    "Connect channels",
    "AI team setup",
  ];
  const setupProgress = Math.round(
    (completedSetupSteps / onboardingSetupSteps.length) * 100,
  );
  const pageTitle =
    step !== 4
      ? stepTitles[step]
      : setupState === "success"
        ? "Your AI Business Team is ready!"
        : setupState === "failed"
          ? "We couldn't finish setting up your workspace"
          : "Setting up your AI Business Team...";
  const pageDescription =
    step === 0
      ? "Tell us a little about your business so your AI team can make better decisions from day one."
      : step === 1
        ? "Give your team the products, services, and availability it should know about."
        : step === 2
          ? "Your AI team will use this voice whenever it writes, replies, or recommends."
          : step === 3
            ? "Choose the channels your team should be ready to work across."
            : setupState === "success"
              ? "Your workspace is configured and your AI team has a clear starting point."
              : setupState === "failed"
                ? "Your onboarding information is still safe. Nothing has been lost."
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
          {stepTitles.map((title, index) => (
            <div
              className={cx(
                "onboarding-step",
                index === step && "active",
                index < step && "complete",
              )}
              key={title}
            >
              <span>{index < step ? <Check size={13} /> : index + 1}</span>
              <small>{title}</small>
            </div>
          ))}
        </div>
        <div className="onboarding-help">Step {step + 1} of 5</div>
      </div>
      <div className="onboarding-progress-line">
        <i
          style={{
            width: `${((step + (step === 4 ? setupProgress / 100 : 0)) / 5) * 100}%`,
          }}
        />
      </div>
      <main className="onboarding-body">
        <div className="onboarding-copy">
          <div className="eyebrow">Welcome to your command room</div>
          <h1>{pageTitle}</h1>
          <p>{pageDescription}</p>
        </div>
        <Card className="onboarding-card" pad={false}>
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
                    {onboardingIndustries.map((industry) => (
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
                    <option>USD · $</option>
                    <option>PKR · ₨</option>
                    <option>EUR · €</option>
                    <option>GBP · £</option>
                  </select>
                </div>
              </div>
            </div>
          )}

          {step === 1 && (
            <ProductCatalogStep catalog={catalog} onChange={setCatalog} />
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
                  const connected = form.channels.includes(name);
                  return (
                    <button
                      key={name}
                      className={cx(
                        "onboarding-channel-card",
                        connected && "connected",
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
                      <span className={cx("channel-toggle", connected && "on")}>
                        <i />
                        {connected ? "Connected" : "Connect"}
                      </span>
                    </button>
                  );
                })}
              </div>
              <div className="onboarding-tip">
                <Link2 size={16} />
                <span>
                  Connections are safe to change later. For this setup, each
                  card is ready as soon as you choose it.
                </span>
              </div>
            </div>
          )}

          {step === 4 && (
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
                  <div className="eyebrow">Setup complete</div>
                  <h2>Your AI Business Team is ready!</h2>
                  <p>
                    Five focused agents are ready to help you move{" "}
                    {form.name || "your business"} forward.
                  </p>
                </div>
              )}

              {setupState === "failed" && (
                <div className="onboarding-failure">
                  <div className="failure-mark">
                    <AlertCircle />
                  </div>
                  <div className="eyebrow">Setup paused</div>
                  <h2>We couldn't finish setting up your workspace</h2>
                  <p>
                    Your onboarding information is still safe. Nothing has been
                    lost.
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

          {notice && step < 4 && (
            <div className="onboarding-notice">
              <AlertCircle size={15} />
              {notice}
            </div>
          )}
          <div className="onboarding-actions">
            {step > 0 && step < 4 && (
              <Button
                variant="secondary"
                onClick={() => {
                  setNotice("");
                  setStep((current) => current - 1);
                }}
                data-testid="button-onboarding-back"
              >
                Back
              </Button>
            )}
            {step === 4 && setupState === "saving" && (
              <Button
                variant="primary"
                disabled
                data-testid="button-onboarding-saving"
              >
                <RefreshCw className="spin" /> Setting up…
              </Button>
            )}
            {step === 4 && setupState === "failed" && (
              <>
                <Button
                  variant="secondary"
                  onClick={backToReview}
                  data-testid="button-onboarding-back-to-review"
                >
                  Back to review
                </Button>
                <Button
                  variant="primary"
                  onClick={retrySetup}
                  data-testid="button-onboarding-retry"
                >
                  Try again <RefreshCw />
                </Button>
              </>
            )}
            {step === 4 && setupState === "success" && (
              <Button
                variant="primary"
                onClick={openDashboard}
                data-testid="button-finish-onboarding"
              >
                Open my dashboard <ArrowRight />
              </Button>
            )}
            {step < 4 && (
              <Button
                variant="green"
                onClick={next}
                disabled={step === 1 && !canContinue}
                data-testid="button-onboarding-next"
              >
                {step === 3 ? "Build my AI team" : "Continue"} <ArrowRight />
              </Button>
            )}
          </div>
        </Card>
      </main>
    </div>
  );
}
