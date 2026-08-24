import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowUpRight,
  CalendarClock,
  Check,
  CreditCard,
  Gauge,
  Info,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Users,
  XCircle,
} from "lucide-react";
import { useBusiness } from "@/business-context";
import { Badge, Button, Card, Modal, PageHeader, SectionTitle } from "@/components/product-ui";
import { humanizeApiError } from "@/services/api-client";
import {
  billingApi,
  shouldRequestPlanChange,
  type BillingPlan,
  type PlanChangeIntent,
} from "@/services/billing";

const usageLabels: Record<string, string> = {
  max_ai_executions_month: "AI executions",
  max_ai_input_tokens_month: "AI input tokens",
  max_ai_output_tokens_month: "AI output tokens",
  max_chatbot_sessions_month: "Chatbot sessions",
  max_chatbot_messages_month: "Chatbot messages",
  max_automation_runs_month: "Automation runs",
  max_businesses: "Businesses",
  max_members: "Active members",
  max_active_workflows: "Active workflows",
  max_integrations: "Connected integrations",
};

type PlanFeedback = {
  businessId: string;
  planCode: string;
  planName: string;
  status: Exclude<PlanChangeIntent["status"], "checkout_ready"> | "error";
  message: string;
  blockers: PlanChangeIntent["blockers"];
};

const informationalUsageLabels: Record<string, string> = {
  chatbot_customer_messages_month: "Customer messages",
  chatbot_ai_responses_month: "AI responses",
};

const featureLabels: Record<string, string> = {
  ai_command_center: "AI Command Center",
  ai_agents: "AI workforce",
  website_chatbot: "Website chatbot",
  automations: "Automations",
  advanced_automations: "Advanced automations",
  marketing_cmo: "AI CMO",
  campaigns: "Campaigns",
  competitor_intelligence: "Competitor intelligence",
  trend_intelligence: "Trend intelligence",
  scheduling: "Scheduling",
  integrations: "Integrations",
  advanced_analytics: "Advanced analytics",
  reports: "Reports",
};

function date(value: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" }).format(new Date(value));
}

function number(value: number) {
  return new Intl.NumberFormat(undefined, { notation: value >= 100_000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(value);
}

function price(plan: BillingPlan, interval: "month" | "year") {
  const minor = interval === "month" ? plan.monthly_price_minor : plan.yearly_price_minor;
  if (minor === null) return "Contact us";
  if (minor === 0) return "Free";
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: plan.currency,
    maximumFractionDigits: 0,
  }).format(minor / 100);
}

export function BillingPage() {
  const queryClient = useQueryClient();
  const {
    activeBusiness,
    activeBusinessId,
    billing,
    billingLoading,
    billingError,
    reloadBilling,
  } = useBusiness();
  const [interval, setInterval] = useState<"month" | "year">("month");
  const [subscriptionMessage, setSubscriptionMessage] = useState("");
  const [actionError, setActionError] = useState("");
  const [busyPlan, setBusyPlan] = useState("");
  const [planFeedback, setPlanFeedback] = useState<PlanFeedback | null>(null);
  const [cancelOpen, setCancelOpen] = useState(false);

  const usage = useQuery({
    queryKey: ["billing", activeBusinessId, "usage"],
    queryFn: ({ signal }) => billingApi.usage(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
  });
  const plans = useQuery({
    queryKey: ["billing", activeBusinessId, "plans"],
    queryFn: ({ signal }) => billingApi.plans(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
  });
  const isOwner = activeBusiness?.membershipRole === "owner";
  const currentPrice = useMemo(
    () => plans.data?.find((plan) => plan.code === billing?.plan_code),
    [billing?.plan_code, plans.data],
  );
  const visiblePlanFeedback = planFeedback?.businessId === activeBusinessId ? planFeedback : null;

  const refreshBillingScreen = useCallback(async () => {
    if (!activeBusinessId) return;
    setPlanFeedback(null);
    await Promise.allSettled([
      reloadBilling(),
      queryClient.invalidateQueries({ queryKey: ["billing", activeBusinessId, "usage"] }),
      queryClient.invalidateQueries({ queryKey: ["billing", activeBusinessId, "plans"] }),
    ]);
  }, [activeBusinessId, queryClient, reloadBilling]);

  useEffect(() => {
    if (!activeBusinessId) return;
    void refreshBillingScreen();
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible") void refreshBillingScreen();
    };
    window.addEventListener("focus", refreshWhenVisible);
    window.addEventListener("pageshow", refreshWhenVisible);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.removeEventListener("focus", refreshWhenVisible);
      window.removeEventListener("pageshow", refreshWhenVisible);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [activeBusinessId, refreshBillingScreen]);

  useEffect(() => {
    setPlanFeedback(null);
    setBusyPlan("");
  }, [activeBusinessId, billing?.plan_code, billing?.plan_version_id]);

  if (!activeBusiness) return null;
  if (billingLoading && !billing) {
    return <div className="empty billing-loading"><RefreshCw className="spin" /><h3>Loading billing</h3><p>Checking this workspace's plan and measured usage…</p></div>;
  }
  if (!billing) {
    return (
      <div className="empty billing-loading">
        <AlertCircle />
        <h3>Billing access is unavailable</h3>
        <p>{billingError || "The server could not verify this workspace's plan. Premium access stays closed until it can."}</p>
        <Button variant="primary" onClick={() => void reloadBilling()}><RefreshCw /> Try again</Button>
      </div>
    );
  }

  const changePlan = async (plan: BillingPlan) => {
    if (!shouldRequestPlanChange(billing.plan_code, plan.code)) {
      setPlanFeedback(null);
      return;
    }
    const businessId = activeBusinessId;
    setSubscriptionMessage(""); setActionError(""); setPlanFeedback(null); setBusyPlan(plan.code);
    try {
      const result = await billingApi.changeIntent(businessId, plan.code, interval);
      if (result.status === "checkout_ready") {
        if (result.checkout_url) {
          window.location.assign(result.checkout_url);
          return;
        }
        setPlanFeedback({
          businessId,
          planCode: plan.code,
          planName: plan.display_name,
          status: "error",
          message: "Checkout could not be started because the billing provider returned no destination.",
          blockers: [],
        });
        return;
      }
      setPlanFeedback({
        businessId,
        planCode: plan.code,
        planName: plan.display_name,
        status: result.status,
        message: result.message,
        blockers: result.blockers,
      });
    } catch (reason) {
      setPlanFeedback({
        businessId,
        planCode: plan.code,
        planName: plan.display_name,
        status: "error",
        message: humanizeApiError(reason, "We couldn't check this plan's availability."),
        blockers: [],
      });
    } finally {
      setBusyPlan("");
    }
  };

  const cancel = async () => {
    setActionError("");
    try {
      await billingApi.cancel(activeBusinessId, "Canceled by the business owner from Billing.");
      await reloadBilling();
      setCancelOpen(false);
      setSubscriptionMessage("Cancellation is scheduled for the end of the current period. No workspace data was removed.");
    } catch (reason) {
      setActionError(humanizeApiError(reason, "We couldn't schedule cancellation."));
    }
  };

  const reactivate = async () => {
      setActionError("");
    try {
      await billingApi.reactivate(activeBusinessId);
      await reloadBilling();
      setSubscriptionMessage("Cancellation was reversed. Your current plan remains active.");
    } catch (reason) {
      setActionError(humanizeApiError(reason, "We couldn't reactivate this subscription."));
    }
  };

  return (
    <div className="billing-page">
      <PageHeader
        eyebrow="Workspace administration"
        title="Billing & plan"
        subtitle="Server-verified access, current-period usage, and plan options for this business."
        action={<Badge tone={billing.subscription_status === "active" ? "green" : "orange"}>{billing.subscription_status}</Badge>}
      />

      {(subscriptionMessage || actionError) && (
        <div className={`billing-notice ${actionError ? "error" : "info"}`} role="status">
          {actionError ? <AlertCircle /> : <Info />}
          <span>{actionError || subscriptionMessage}</span>
        </div>
      )}

      <Card className="billing-hero">
        <div className="billing-plan-mark"><CreditCard /></div>
        <div className="billing-hero-copy">
          <div className="eyebrow">Current plan</div>
          <h2>{billing.plan_name}</h2>
          <p>
            {currentPrice ? price(currentPrice, billing.billing_interval) : "Grandfathered access"}
            {currentPrice?.monthly_price_minor ? ` / ${billing.billing_interval}` : ""}
            <span> · Version {billing.plan_version}</span>
          </p>
        </div>
        <div className="billing-period">
          <CalendarClock />
          <div><span>Current period</span><strong>{date(billing.current_period_start)} – {date(billing.current_period_end)}</strong></div>
        </div>
        {billing.trial_ends_at && <div className="billing-period"><Sparkles /><div><span>Trial ends</span><strong>{date(billing.trial_ends_at)}</strong></div></div>}
        <div className="billing-hero-actions">
          {billing.cancel_at_period_end ? (
            <Button onClick={() => void reactivate()} disabled={!isOwner}>Reactivate plan</Button>
          ) : billing.plan_code !== "free" ? (
            <Button variant="ghost" onClick={() => setCancelOpen(true)} disabled={!isOwner}>Cancel at period end</Button>
          ) : null}
        </div>
      </Card>

      {billing.cancel_at_period_end && (
        <div className="billing-notice warning"><CalendarClock /><span>Cancellation is scheduled for {date(billing.current_period_end)}. Access remains unchanged until then.</span></div>
      )}
      {!billing.provider_configured && (
        <div className="billing-notice neutral"><ShieldCheck /><span>Online checkout is not configured yet. Plan buttons will explain availability without changing your subscription.</span></div>
      )}

      <section>
        <SectionTitle title="Current-period usage" action={<span className="subtle">Renews {date(billing.current_period_end)}</span>} />
        {usage.isLoading ? (
          <Card className="billing-query-state"><RefreshCw className="spin" /> Measuring durable usage…</Card>
        ) : usage.error || !usage.data ? (
          <Card className="billing-query-state error"><AlertCircle /> Usage is temporarily unavailable.</Card>
        ) : (
          <>
            <div className="billing-usage-grid">
              {Object.entries(usage.data.usage).map(([key, consumed]) => {
                const limit = usage.data!.limits[key] ?? 0;
                const percent = limit > 0 ? Math.min(100, Math.round((consumed / limit) * 100)) : 100;
                return (
                  <Card key={key} className="billing-usage-card">
                    <div className="billing-usage-head"><span>{key === "max_members" ? <Users /> : <Gauge />}{usageLabels[key] ?? key}</span><strong>{number(consumed)} <small>/ {number(limit)}</small></strong></div>
                    <div className="billing-meter" aria-label={`${percent}% used`}><i style={{ width: `${percent}%` }} data-level={percent >= 100 ? "full" : percent >= 80 ? "high" : "normal"} /></div>
                    <div className="billing-usage-foot"><span>{percent}% used</span><span>{number(usage.data!.remaining[key] ?? 0)} remaining</span></div>
                  </Card>
                );
              })}
            </div>
            {Object.keys(usage.data.informational).length > 0 && (
              <div className="billing-usage-detail" aria-label="Measured usage detail">
                {Object.entries(usage.data.informational).map(([key, value]) => (
                  <span key={key}><strong>{number(value)}</strong> {informationalUsageLabels[key] ?? key}</span>
                ))}
              </div>
            )}
          </>
        )}
      </section>

      <section>
        <SectionTitle
          title="Compare plans"
          action={<div className="billing-interval" aria-label="Billing interval"><button className={interval === "month" ? "active" : ""} onClick={() => { setInterval("month"); setPlanFeedback(null); }}>Monthly</button><button className={interval === "year" ? "active" : ""} onClick={() => { setInterval("year"); setPlanFeedback(null); }}>Yearly</button></div>}
        />
        {plans.isLoading ? <Card className="billing-query-state"><RefreshCw className="spin" /> Loading plans…</Card> : plans.error ? <Card className="billing-query-state error"><AlertCircle /> Plans are temporarily unavailable.</Card> : (
          <div className="billing-plan-grid">
            {plans.data?.map((plan) => {
              const current = plan.code === billing.plan_code;
              const cardFeedback = visiblePlanFeedback?.planCode === plan.code ? visiblePlanFeedback : null;
              const features = Object.entries(plan.entitlements).filter(([key, value]) => value === true && featureLabels[key]).slice(0, 6);
              return (
                <Card key={plan.version_id} className={`billing-plan-card ${current ? "current" : ""}`}>
                  {current && <div className="billing-current-ribbon">Current plan</div>}
                  <div className="billing-plan-top"><h3>{plan.display_name}</h3><p>{plan.description}</p></div>
                  <div className="billing-price"><strong>{price(plan, interval)}</strong>{(interval === "month" ? plan.monthly_price_minor : plan.yearly_price_minor) !== null && (interval === "month" ? plan.monthly_price_minor : plan.yearly_price_minor) !== 0 && <span>/ {interval}</span>}</div>
                  {plan.trial_days > 0 && <div className="billing-trial-copy">{plan.trial_days}-day trial term</div>}
                  <ul>{features.map(([key]) => <li key={key}><Check /> {featureLabels[key]}</li>)}</ul>
                  <div className="billing-plan-limits"><span>{number(Number(plan.entitlements.max_ai_executions_month ?? 0))} AI runs</span><span>{number(Number(plan.entitlements.max_members ?? 0))} members</span></div>
                  <Button variant={current ? "secondary" : "primary"} disabled={current || !isOwner || Boolean(busyPlan)} onClick={() => void changePlan(plan)}>{busyPlan === plan.code ? <RefreshCw className="spin" /> : current ? <Check /> : <ArrowUpRight />}{current ? "Selected" : billing.provider_configured ? "Choose plan" : "Check availability"}</Button>
                  {cardFeedback && (
                    <div
                      className={`billing-notice billing-plan-feedback ${cardFeedback.status === "error" ? "error" : cardFeedback.status === "blocked" ? "warning" : "neutral"}`}
                      data-testid={`plan-change-feedback-${plan.code}`}
                      role="status"
                      aria-live="polite"
                    >
                      {cardFeedback.status === "provider_unavailable" ? <ShieldCheck /> : <AlertCircle />}
                      <div className="billing-plan-feedback-copy">
                        <strong>{cardFeedback.status === "blocked" ? `${cardFeedback.planName} is blocked by current usage` : cardFeedback.status === "provider_unavailable" ? `${cardFeedback.planName} checkout requires provider setup` : `${cardFeedback.planName} availability check failed`}</strong>
                        <span>{cardFeedback.message}</span>
                        {cardFeedback.blockers.length > 0 && (
                          <ul className="billing-plan-blockers">
                            {cardFeedback.blockers.map((blocker) => (
                              <li key={blocker.entitlement_key}><strong>{usageLabels[blocker.entitlement_key] ?? blocker.entitlement_key.replaceAll("_", " ")}</strong>: {number(blocker.current)} currently in use; {number(blocker.target_limit)} allowed on this plan.</li>
                            ))}
                          </ul>
                        )}
                      </div>
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        )}
        {!isOwner && <p className="billing-owner-note"><ShieldCheck /> Only a business owner can change or cancel this plan. Usage and plan details remain visible to active members.</p>}
        <p className="billing-commercial-note">Prices are initial catalog metadata and require commercial review before payment-provider launch. Taxes, invoices, payment methods, MRR, and ARR are not shown because no provider data exists.</p>
      </section>

      {cancelOpen && (
        <Modal title="Cancel at period end?" description={`Your ${billing.plan_name} access remains active through ${date(billing.current_period_end)}.`} onClose={() => setCancelOpen(false)}>
          <div className="billing-cancel-copy"><XCircle /><div><strong>No data will be deleted.</strong><p>The workspace falls back to the explicit Free baseline only when the current period ends. You can reactivate before then.</p></div></div>
          <div className="modal-foot"><Button onClick={() => setCancelOpen(false)}>Keep plan</Button><Button variant="danger" onClick={() => void cancel()}>Schedule cancellation</Button></div>
        </Modal>
      )}
    </div>
  );
}
