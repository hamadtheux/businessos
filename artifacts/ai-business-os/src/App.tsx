import { useEffect, type ComponentType, type ReactNode } from "react";
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
import {
  AgentActivityPage,
  AgentDetailPage,
  AgentsOverviewPage,
} from "@/features/agents/agent-pages";
import { AnalyticsPage } from "@/features/analytics/analytics-page";
import { AuditLogPage } from "@/features/audit/audit-log";
import { AuthProvider, useAuth } from "@/features/auth/auth-context";
import { LoginPage, RegisterPage } from "@/features/auth/auth-pages";
import { WorkflowBuilderPage } from "@/features/automations/workflow-builder";
import { BusinessBrainPage } from "@/features/brain/business-brain-page";
import { IndustryWorkspacePage } from "@/features/catalog/industry-workspace-page";
import { CommandCenterPage } from "@/features/command/command-center-page";
import { BusinessDashboardPage } from "@/features/dashboard/dashboard-page";
import {
  ApprovalsPage,
  OpportunitiesPage,
} from "@/features/governance/action-pages";
import { IntegrationsPage } from "@/features/integrations/integrations-page";
import {
  CompetitorIntelligencePage,
  TrendIntelligencePage,
} from "@/features/intelligence/intelligence-pages";
import { CmoPage } from "@/features/marketing/cmo-page";
import {
  CampaignsPage,
  SocialManagementPage,
} from "@/features/marketing/marketing-pages";
import { OnboardingPage } from "@/features/onboarding/onboarding-page";
import {
  ConversationsPage,
  CrmPage,
  CustomersPage,
  OrdersPage,
} from "@/features/operations/operation-pages";
import { DailyReportPage } from "@/features/reports/daily-report";
import { SettingsPage } from "@/features/settings/settings-page";
import {
  isApplicationBootstrapping,
  nextProtectedRoute,
} from "@/services/app-routing";
import { demoWorkspaceDataEnabled } from "@/services/workspace-repository";

const queryClient = new QueryClient();

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

function UnsupportedWorkspaceModule() {
  return (
    <div className="empty" style={{ minHeight: "55dvh" }}>
      <Sparkles />
      <h3>This workspace module is ready for its backend API</h3>
      <p>
        No sample records are shown in normal mode. Enable the explicit local
        demo-data flag only when you need to review the approved prototype.
      </p>
    </div>
  );
}

function workspaceModule(component: ComponentType) {
  return demoWorkspaceDataEnabled ? component : UnsupportedWorkspaceModule;
}

function RoutedErrorBoundary({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  return <ErrorBoundary resetKey={location}>{children}</ErrorBoundary>;
}

function AppRoutes() {
  return (
    <RoutedErrorBoundary>
      <Switch>
        <Route path="/" component={Home} />
        <Route path="/login" component={LoginPage} />
        <Route path="/register" component={RegisterPage} />
        <Route path="/onboarding" component={OnboardingPage} />
        <Route path="/dashboard" component={BusinessDashboardPage} />
        <Route path="/command" component={workspaceModule(CommandCenterPage)} />
        <Route
          path="/command-center"
          component={workspaceModule(CommandCenterPage)}
        />
        <Route
          path="/conversations"
          component={workspaceModule(ConversationsPage)}
        />
        <Route path="/orders" component={workspaceModule(OrdersPage)} />
        <Route path="/customers" component={workspaceModule(CustomersPage)} />
        <Route path="/crm" component={workspaceModule(CrmPage)} />
        <Route path="/cmo" component={workspaceModule(CmoPage)} />
        <Route path="/marketing" component={workspaceModule(CmoPage)} />
        <Route path="/campaigns" component={workspaceModule(CampaignsPage)} />
        <Route
          path="/social"
          component={workspaceModule(SocialManagementPage)}
        />
        <Route
          path="/competitors"
          component={workspaceModule(CompetitorIntelligencePage)}
        />
        <Route
          path="/trends"
          component={workspaceModule(TrendIntelligencePage)}
        />
        <Route
          path="/agents/:agentId/activity"
          component={workspaceModule(AgentActivityPage)}
        />
        <Route
          path="/agents/activity"
          component={workspaceModule(AgentActivityPage)}
        />
        <Route
          path="/agents/:agentId"
          component={workspaceModule(AgentDetailPage)}
        />
        <Route path="/agents" component={workspaceModule(AgentsOverviewPage)} />
        <Route
          path="/automations"
          component={workspaceModule(WorkflowBuilderPage)}
        />
        <Route path="/approvals" component={workspaceModule(ApprovalsPage)} />
        <Route
          path="/opportunities"
          component={workspaceModule(OpportunitiesPage)}
        />
        <Route path="/analytics" component={workspaceModule(AnalyticsPage)} />
        <Route
          path="/integrations"
          component={workspaceModule(IntegrationsPage)}
        />
        <Route path="/brain" component={BusinessBrainPage} />
        <Route
          path="/reports/daily"
          component={workspaceModule(DailyReportPage)}
        />
        <Route path="/audit" component={workspaceModule(AuditLogPage)} />
        <Route path="/inventory" component={UnsupportedWorkspaceModule} />
        <Route path="/properties" component={IndustryWorkspacePage} />
        <Route path="/products" component={IndustryWorkspacePage} />
        <Route path="/settings" component={SettingsPage} />
        <Route component={NotFound} />
      </Switch>
    </RoutedErrorBoundary>
  );
}

function RoutedApp() {
  const [location, setLocation] = useLocation();
  const { user, status } = useAuth();
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
  if (status === "authenticated" && businessesError) {
    return (
      <div className="empty full-screen-loading">
        <AlertCircle />
        <h3>We couldn't open your businesses</h3>
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
