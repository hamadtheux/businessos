import {
  lazy,
  Suspense,
  useEffect,
  type ComponentType,
  type ReactNode,
} from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AlertCircle, RefreshCw, Sparkles } from "lucide-react";
import {
  Link,
  Route,
  Router as WouterRouter,
  Switch,
  useLocation,
} from "wouter";
import { BusinessProvider, useBusiness } from "@/business-context";
import { AppShell } from "@/components/app-shell";
import { ErrorBoundary } from "@/components/error-boundary";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider, useAuth } from "@/features/auth/auth-context";
import { LoginPage, RegisterPage } from "@/features/auth/auth-pages";
import { OnboardingPage } from "@/features/onboarding/onboarding-page";
import {
  businessFeatureRouteRedirect,
  isBusinessFeatureEnabled,
  type BusinessFeature,
} from "@/lib/business-features";
import {
  getIndustryWorkspaceProfile,
  isWorkspaceModuleVisible,
  type WorkspaceModule,
} from "@/lib/industry-workspaces";
import {
  isApplicationBootstrapping,
  nextProtectedRoute,
} from "@/services/app-routing";

const queryClient = new QueryClient();

function lazyNamed(
  load: () => Promise<Record<string, unknown>>,
  exportName: string,
) {
  return lazy(async () => {
    const module = await load();
    return { default: module[exportName] as ComponentType };
  });
}

const AgentsOverviewPage = lazyNamed(() => import("@/features/agents/agent-pages"), "AgentsOverviewPage");
const AgentActivityPage = lazyNamed(() => import("@/features/agents/agent-pages"), "AgentActivityPage");
const AgentDetailPage = lazyNamed(() => import("@/features/agents/agent-pages"), "AgentDetailPage");
const AnalyticsPage = lazyNamed(() => import("@/features/analytics/analytics-page"), "AnalyticsPage");
const AuditLogPage = lazyNamed(() => import("@/features/audit/audit-log"), "AuditLogPage");
const WorkflowBuilderPage = lazyNamed(() => import("@/features/automations/workflow-builder"), "WorkflowBuilderPage");
const BusinessBrainPage = lazyNamed(() => import("@/features/brain/business-brain-page"), "BusinessBrainPage");
const ChatbotPage = lazyNamed(() => import("@/features/chatbot/chatbot-page"), "ChatbotPage");
const IndustryWorkspacePage = lazyNamed(() => import("@/features/catalog/industry-workspace-page"), "IndustryWorkspacePage");
const CommandCenterPage = lazyNamed(() => import("@/features/command/command-center-page"), "CommandCenterPage");
const BusinessDashboardPage = lazyNamed(() => import("@/features/dashboard/dashboard-page"), "BusinessDashboardPage");
const ApprovalsPage = lazyNamed(() => import("@/features/governance/action-pages"), "ApprovalsPage");
const OpportunitiesPage = lazyNamed(() => import("@/features/governance/opportunities-page"), "OpportunitiesPage");
const IntegrationsPage = lazyNamed(() => import("@/features/integrations/integrations-page"), "IntegrationsPage");
const CompetitorIntelligencePage = lazyNamed(() => import("@/features/intelligence/intelligence-pages"), "CompetitorIntelligencePage");
const TrendIntelligencePage = lazyNamed(() => import("@/features/intelligence/intelligence-pages"), "TrendIntelligencePage");
const CmoPage = lazyNamed(() => import("@/features/marketing/cmo-page"), "CmoPage");
const CampaignsPage = lazyNamed(() => import("@/features/marketing/marketing-pages"), "CampaignsPage");
const SocialManagementPage = lazyNamed(() => import("@/features/marketing/marketing-pages"), "SocialManagementPage");
const ConversationsPage = lazyNamed(() => import("@/features/operations/operation-pages"), "ConversationsPage");
const CrmPage = lazyNamed(() => import("@/features/operations/operation-pages"), "CrmPage");
const CustomersPage = lazyNamed(() => import("@/features/operations/operation-pages"), "CustomersPage");
const OrdersPage = lazyNamed(() => import("@/features/operations/operation-pages"), "OrdersPage");
const DailyReportPage = lazyNamed(() => import("@/features/reports/daily-report"), "DailyReportPage");
const SchedulingPage = lazyNamed(() => import("@/features/scheduling/scheduling-page"), "SchedulingPage");
const SettingsPage = lazyNamed(() => import("@/features/settings/settings-page"), "SettingsPage");
const BillingPage = lazyNamed(() => import("@/features/billing/billing-page"), "BillingPage");

function Home() {
  const [, setLocation] = useLocation();

  useEffect(() => {
    setLocation("/dashboard");
  }, [setLocation]);

  return (
    <div className="empty">
      <Sparkles />
      <h3>Opening your command room</h3>
      <p>Loading your business workspace…</p>
    </div>
  );
}

function NotFound() {
  return (
    <div className="empty" style={{ minHeight: "80dvh" }}>
      <AlertCircle />
      <h3>That page is not on the map</h3>
      <p>Return to the dashboard to continue.</p>
      <Link
        href="/dashboard"
        className="btn btn-green"
        data-testid="link-back-dashboard"
      >
        Back to dashboard
      </Link>
    </div>
  );
}

function RoutedErrorBoundary({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  return <ErrorBoundary resetKey={location}>{children}</ErrorBoundary>;
}

function BusinessFeatureRoute({
  feature,
  component: Component,
}: {
  feature: BusinessFeature;
  component: ComponentType;
}) {
  const [location, setLocation] = useLocation();
  const { activeBusiness, billing, billingLoading } = useBusiness();
  const enabled = isBusinessFeatureEnabled(
    activeBusiness,
    feature,
    billing?.entitlements ?? null,
  );
  const redirect = businessFeatureRouteRedirect(activeBusiness, location);

  useEffect(() => {
    if (!activeBusiness || billingLoading) return;
    if (redirect) setLocation(redirect);
    else if (!enabled) setLocation(`/billing?feature=${feature}`);
  }, [activeBusiness, billingLoading, enabled, feature, redirect, setLocation]);

  if (billingLoading) {
    return <div className="empty"><RefreshCw className="spin" /><h3>Checking plan access</h3></div>;
  }
  return enabled ? <Component /> : null;
}

function SchedulingRoute() {
  return (
    <BusinessFeatureRoute
      feature="scheduling"
      component={SchedulingPage}
    />
  );
}

function WorkspaceModuleRoute({
  module,
  component: Component,
}: {
  module: WorkspaceModule;
  component: ComponentType;
}) {
  const [, setLocation] = useLocation();
  const { activeBusiness } = useBusiness();
  const enabled = isWorkspaceModuleVisible(activeBusiness?.industry, module);

  useEffect(() => {
    if (!activeBusiness) return;
    if (!enabled) setLocation("/dashboard");
  }, [activeBusiness, enabled, setLocation]);

  return enabled ? <Component /> : null;
}

function CatalogWorkspaceRoute({
  expectedRoute,
}: {
  expectedRoute: "/products" | "/properties";
}) {
  const [, setLocation] = useLocation();
  const { activeBusiness } = useBusiness();
  const profile = getIndustryWorkspaceProfile(activeBusiness?.industry);

  const enabled =
    isWorkspaceModuleVisible(activeBusiness?.industry, "catalog") &&
    profile.catalogRoute === expectedRoute;

  useEffect(() => {
    if (!activeBusiness) return;
    if (!enabled) setLocation("/dashboard");
  }, [activeBusiness, enabled, setLocation]);

  return enabled ? <IndustryWorkspacePage /> : null;
}

const ConversationsRoute = () => (
  <WorkspaceModuleRoute module="conversations" component={ConversationsPage} />
);

const OrdersRoute = () => (
  <WorkspaceModuleRoute module="orders" component={OrdersPage} />
);

const CustomersRoute = () => (
  <WorkspaceModuleRoute module="customers" component={CustomersPage} />
);

const CrmRoute = () => (
  <WorkspaceModuleRoute module="crm" component={CrmPage} />
);

const CommandRoute = () => <BusinessFeatureRoute feature="ai_command_center" component={CommandCenterPage} />;
const ReportsRoute = () => <BusinessFeatureRoute feature="reports" component={DailyReportPage} />;
const ChatbotRoute = () => <BusinessFeatureRoute feature="website_chatbot" component={ChatbotPage} />;
const CmoRoute = () => <BusinessFeatureRoute feature="marketing_cmo" component={CmoPage} />;
const CampaignsRoute = () => <BusinessFeatureRoute feature="campaigns" component={CampaignsPage} />;
const SocialRoute = () => <BusinessFeatureRoute feature="campaigns" component={SocialManagementPage} />;
const AgentsRoute = () => <BusinessFeatureRoute feature="ai_agents" component={AgentsOverviewPage} />;
const AgentActivityRoute = () => <BusinessFeatureRoute feature="ai_agents" component={AgentActivityPage} />;
const AgentDetailRoute = () => <BusinessFeatureRoute feature="ai_agents" component={AgentDetailPage} />;
const AutomationsRoute = () => <BusinessFeatureRoute feature="automations" component={WorkflowBuilderPage} />;
const AnalyticsRoute = () => <BusinessFeatureRoute feature="advanced_analytics" component={AnalyticsPage} />;
const CompetitorsRoute = () => <BusinessFeatureRoute feature="competitor_intelligence" component={CompetitorIntelligencePage} />;
const TrendsRoute = () => <BusinessFeatureRoute feature="trend_intelligence" component={TrendIntelligencePage} />;
const IntegrationsRoute = () => <BusinessFeatureRoute feature="integrations" component={IntegrationsPage} />;

function AppRoutes() {
  return (
    <RoutedErrorBoundary>
      <Suspense fallback={<div className="empty"><RefreshCw className="spin" /><h3>Opening workspace</h3></div>}>
      <Switch>
        <Route path="/" component={Home} />
        <Route path="/login" component={LoginPage} />
        <Route path="/register" component={RegisterPage} />
        <Route path="/onboarding" component={OnboardingPage} />
        <Route path="/dashboard" component={BusinessDashboardPage} />
        <Route path="/command" component={CommandRoute} />
        <Route
          path="/command-center"
          component={CommandRoute}
        />
        <Route path="/conversations" component={ConversationsRoute} />
        <Route path="/orders" component={OrdersRoute} />
        <Route path="/customers" component={CustomersRoute} />
        <Route path="/crm" component={CrmRoute} />
        <Route path="/scheduling" component={SchedulingRoute} />
        <Route path="/cmo" component={CmoRoute} />
        <Route path="/marketing" component={CmoRoute} />
        <Route path="/marketing/content" component={SocialRoute} />
        <Route path="/marketing/calendar" component={SocialRoute} />
        <Route path="/marketing/campaigns" component={CampaignsRoute} />
        <Route path="/marketing/social" component={SocialRoute} />
        <Route path="/marketing/performance" component={AnalyticsRoute} />
        <Route path="/campaigns" component={CampaignsRoute} />
        <Route path="/social" component={SocialRoute} />
        <Route path="/competitors" component={CompetitorsRoute} />
        <Route path="/trends" component={TrendsRoute} />
        <Route
          path="/agents/:agentId/activity"
          component={AgentActivityRoute}
        />
        <Route
          path="/agents/activity"
          component={AgentActivityRoute}
        />
        <Route
          path="/agents/:agentId"
          component={AgentDetailRoute}
        />
        <Route path="/agents" component={AgentsRoute} />
        <Route path="/chatbot" component={ChatbotRoute} />
        <Route path="/automations" component={AutomationsRoute} />
        <Route path="/approvals" component={ApprovalsPage} />
        <Route path="/opportunities" component={OpportunitiesPage} />
        <Route path="/analytics" component={AnalyticsRoute} />
        <Route
          path="/integrations"
          component={IntegrationsRoute}
        />
        <Route path="/brain" component={BusinessBrainPage} />
        <Route path="/business-brain" component={BusinessBrainPage} />
        <Route path="/reports/daily" component={ReportsRoute} />
        <Route path="/daily-report" component={ReportsRoute} />
        <Route path="/audit" component={AuditLogPage} />
        <Route
          path="/properties"
          component={() => <CatalogWorkspaceRoute expectedRoute="/properties" />}
        />
        <Route
          path="/products"
          component={() => <CatalogWorkspaceRoute expectedRoute="/products" />}
        />
        <Route
          path="/catalog"
          component={() => (
            <WorkspaceModuleRoute
              module="catalog"
              component={IndustryWorkspacePage}
            />
          )}
        />
        <Route path="/settings" component={SettingsPage} />
        <Route path="/billing" component={BillingPage} />
        <Route component={NotFound} />
      </Switch>
      </Suspense>
    </RoutedErrorBoundary>
  );
}

function RoutedApp() {
  const [location, setLocation] = useLocation();
  const {
    user,
    status,
    error: authError,
    retryBootstrap,
  } = useAuth();
  const {
    businesses,
    isLoading: businessesLoading,
    error: businessesError,
    reloadBusinesses,
  } = useBusiness();
  const publicRoute = location === "/login" || location === "/register";
  const bootstrapping = isApplicationBootstrapping(status, businessesLoading);

  useEffect(() => {
    const nextRoute = nextProtectedRoute({
      status,
      businessesLoading,
      businessesError,
      businessCount: businesses.length,
      location,
    });
    if (nextRoute) setLocation(nextRoute);
  }, [
    businesses.length,
    businessesError,
    businessesLoading,
    location,
    setLocation,
    status,
  ]);

  if (bootstrapping) {
    return (
      <div className="empty full-screen-loading">
        <RefreshCw className="spin" />
        <h3>Opening AI Business OS</h3>
        <p>Restoring your secure workspace…</p>
      </div>
    );
  }
  if (status === "recoverable_error") {
    return (
      <div className="empty full-screen-loading">
        <AlertCircle />
        <h3>Cannot open AI Business OS</h3>
        <p>{authError}</p>
        <button className="btn btn-green" onClick={retryBootstrap}>
          <RefreshCw /> Retry connection
        </button>
      </div>
    );
  }
  if (status === "authenticated" && businessesError) {
    return (
      <div className="empty full-screen-loading">
        <AlertCircle />
        <h3>Business data could not load</h3>
        <p>{businessesError}</p>
        <button
          className="btn btn-green"
          onClick={() => void reloadBusinesses().catch(() => undefined)}
        >
          <RefreshCw /> Try again
        </button>
      </div>
    );
  }
  if (status === "unauthenticated" && publicRoute) return <AppRoutes />;
  if (!user || status !== "authenticated") {
    return (
      <div className="empty full-screen-loading">
        <RefreshCw className="spin" />
        <h3>Opening sign in</h3>
      </div>
    );
  }
  return location === "/onboarding" ? (
    <AppRoutes />
  ) : (
    <AppShell>
      <AppRoutes />
    </AppShell>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <AuthProvider>
            <BusinessProvider>
              <RoutedApp />
            </BusinessProvider>
          </AuthProvider>
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
