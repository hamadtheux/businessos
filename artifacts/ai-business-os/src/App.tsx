import { useEffect, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AlertCircle, RefreshCw, Sparkles } from "lucide-react";
import {
  Link,
  Route,
  Router as WouterRouter,
  Switch,
  useLocation,
} from "wouter";
import { BusinessProvider } from "@/business-context";
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
        <Route path="/command" component={CommandCenterPage} />
        <Route path="/command-center" component={CommandCenterPage} />
        <Route path="/conversations" component={ConversationsPage} />
        <Route path="/orders" component={OrdersPage} />
        <Route path="/customers" component={CustomersPage} />
        <Route path="/crm" component={CrmPage} />
        <Route path="/cmo" component={CmoPage} />
        <Route path="/marketing" component={CmoPage} />
        <Route path="/campaigns" component={CampaignsPage} />
        <Route path="/social" component={SocialManagementPage} />
        <Route path="/competitors" component={CompetitorIntelligencePage} />
        <Route path="/trends" component={TrendIntelligencePage} />
        <Route path="/agents/:agentId/activity" component={AgentActivityPage} />
        <Route path="/agents/activity" component={AgentActivityPage} />
        <Route path="/agents/:agentId" component={AgentDetailPage} />
        <Route path="/agents" component={AgentsOverviewPage} />
        <Route path="/automations" component={WorkflowBuilderPage} />
        <Route path="/approvals" component={ApprovalsPage} />
        <Route path="/opportunities" component={OpportunitiesPage} />
        <Route path="/analytics" component={AnalyticsPage} />
        <Route path="/integrations" component={IntegrationsPage} />
        <Route path="/brain" component={BusinessBrainPage} />
        <Route path="/reports/daily" component={DailyReportPage} />
        <Route path="/audit" component={AuditLogPage} />
        <Route path="/inventory" component={IndustryWorkspacePage} />
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
  const { user, isLoading } = useAuth();
  const publicRoute = location === "/login" || location === "/register";

  useEffect(() => {
    if (!isLoading && !user && !publicRoute) setLocation("/login");
  }, [isLoading, publicRoute, setLocation, user]);

  if (isLoading) {
    return (
      <div className="empty full-screen-loading">
        <RefreshCw className="spin" />
        <h3>Opening AI Business OS</h3>
        <p>Restoring your prototype session…</p>
      </div>
    );
  }
  if (publicRoute) return <AppRoutes />;
  if (!user) {
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
