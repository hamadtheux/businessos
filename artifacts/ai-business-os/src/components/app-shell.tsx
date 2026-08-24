import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  Bell,
  CalendarDays,
  Bot,
  Brain,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  ClipboardCheck,
  CreditCard,
  FileText,
  Globe2,
  Inbox,
  LayoutDashboard,
  Lightbulb,
  Link2,
  LogOut,
  Menu,
  MessageCircle,
  Package,
  PanelLeftClose,
  Plus,
  RefreshCw,
  ScrollText,
  Search,
  Settings2,
  ShoppingBag,
  Sparkles,
  Target,
  TrendingUp,
  Users,
  Wand2,
  X,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useBusiness } from "@/business-context";
import { Avatar, Button, Modal } from "@/components/product-ui";
import { BusinessBrandMark } from "@/features/branding/branding-editor";
import { useAuth, userDisplayName } from "@/features/auth/auth-context";
import { NotificationCenter } from "@/features/notifications/notification-center";
import { brandThemeStyle, deriveBrandTheme } from "@/lib/brand-theme";
import { filterBusinessFeatureItems, isBusinessFeatureEnabled, type BusinessFeature } from "@/lib/business-features";
import {
  getIndustryWorkspaceProfile,
  isWorkspaceModuleVisible,
  type WorkspaceModule,
} from "@/lib/industry-workspaces";
import { cx } from "@/lib/product-utils";
import { operationsApi } from "@/services/operations";

type NavItem = { href: string; label: string; icon: LucideIcon; feature?: BusinessFeature };
type NavGroup = { label: string; items: NavItem[] };

const NAV_MODULE_BY_HREF: Readonly<Record<string, WorkspaceModule>> = {
  "/dashboard": "dashboard",
  "/command": "ai_command_center",
  "/reports/daily": "daily_report",
  "/conversations": "conversations",
  "/orders": "orders",
  "/customers": "customers",
  "/crm": "crm",
  "/scheduling": "scheduling",
  "/chatbot": "website_chatbot",
  "/cmo": "marketing_cmo",
  "/agents": "ai_agents",
  "/automations": "automations",
  "/approvals": "approvals",
  "/opportunities": "opportunities",
  "/analytics": "analytics",
  "/competitors": "competitors",
  "/trends": "trends",
  "/integrations": "integrations",
  "/brain": "business_brain",
  "/audit": "audit",
};

const navGroups: NavGroup[] = [
  {
    label: "Overview",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      { href: "/command", label: "AI Command Center", icon: Sparkles, feature: "ai_command_center" as const },
      { href: "/reports/daily", label: "Daily AI Report", icon: FileText, feature: "reports" as const },
    ],
  },
  {
    label: "Operations",
    items: [
      { href: "/conversations", label: "Conversations", icon: Inbox },
      { href: "/orders", label: "Orders", icon: ShoppingBag },
      { href: "/customers", label: "Customers", icon: Users },
      { href: "/crm", label: "Leads & CRM", icon: Target },
      {
        href: "/scheduling",
        label: "Scheduling",
        icon: CalendarDays,
        feature: "scheduling" as const,
      },
    ],
  },
  {
    label: "AI Team",
    items: [
      { href: "/chatbot", label: "Website Chatbot", icon: MessageCircle, feature: "website_chatbot" as const },
      { href: "/cmo", label: "AI CMO", icon: Wand2, feature: "marketing_cmo" as const },
      { href: "/agents", label: "AI Agents", icon: Bot, feature: "ai_agents" as const },
      { href: "/automations", label: "Automations", icon: Zap, feature: "automations" as const },
      { href: "/approvals", label: "Approvals", icon: ClipboardCheck },
      { href: "/opportunities", label: "Opportunities", icon: Lightbulb },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { href: "/analytics", label: "Analytics", icon: BarChart3, feature: "advanced_analytics" as const },
      { href: "/competitors", label: "Competitors", icon: Target, feature: "competitor_intelligence" as const },
      { href: "/trends", label: "Trends", icon: TrendingUp, feature: "trend_intelligence" as const },
      { href: "/integrations", label: "Integrations", icon: Link2, feature: "integrations" as const },
      { href: "/brain", label: "Business Brain", icon: Brain },
      { href: "/audit", label: "Audit Log", icon: ScrollText },
    ],
  },
];

type ToastItem = { id: number; message: string };

function Toasts({
  items,
  remove,
}: {
  items: ToastItem[];
  remove: (id: number) => void;
}) {
  return (
    <div className="toast-stack">
      {items.map((item) => (
        <div
          className="toast"
          key={item.id}
          data-testid={`status-toast-${item.id}`}
        >
          <CheckCircle2 />
          <span>{item.message}</span>
          <button
            className="toast-close"
            onClick={() => remove(item.id)}
            aria-label="Dismiss notification"
          >
            <X />
          </button>
        </div>
      ))}
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [location, setLocation] = useLocation();
  const [open, setOpen] = useState(false);
  const [command, setCommand] = useState("");
  const [businessOpen, setBusinessOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const { user, logout } = useAuth();
  const {
    businesses,
    activeBusiness,
    activeBusinessId,
    selectBusiness,
    isLoading,
    billing,
  } = useBusiness();
  const notifications = useQuery({
    queryKey: ["operations", activeBusinessId, "notifications", "unread"],
    queryFn: ({ signal }) => operationsApi.notifications.list(activeBusinessId, true, signal),
    enabled: Boolean(activeBusinessId),
    refetchInterval: 60_000,
  });
  const unreadNotifications = notifications.data?.total ?? 0;
  const displayName = userDisplayName(user);
  const membershipRole = activeBusiness?.membershipRole
    ? `${activeBusiness.membershipRole.charAt(0).toUpperCase()}${activeBusiness.membershipRole.slice(1)}`
    : "Member";
  const canRunCommands = isBusinessFeatureEnabled(activeBusiness, "ai_command_center", billing?.entitlements ?? null);
  const canAutomate = isBusinessFeatureEnabled(activeBusiness, "automations", billing?.entitlements ?? null);
  const legacyTheme = activeBusiness?.theme === "navy" ? "navy" : "green";
  const brandTheme = deriveBrandTheme(
    activeBusiness?.brandIdentity,
    legacyTheme,
  );
  const workspaceProfile = getIndustryWorkspaceProfile(activeBusiness?.industry);
  const terminology = workspaceProfile.terminology;
  const industryNav =
    workspaceProfile.catalogRoute && workspaceProfile.catalogLabel
      ? {
          href: workspaceProfile.catalogRoute,
          label: workspaceProfile.catalogLabel,
          icon: Package,
        }
      : null;
  const IndustryIcon = industryNav?.icon;
  const notify = (message: string) => {
    const id = Date.now();
    setToasts((current) => [...current, { id, message }]);
    window.setTimeout(
      () => setToasts((current) => current.filter((item) => item.id !== id)),
      3200,
    );
  };
  const runCommand = (event: FormEvent) => {
    event.preventDefault();
    if (!command.trim()) return;
    if (!canRunCommands) {
      setLocation("/billing?feature=ai_command_center");
      return;
    }
    setCommand("");
    setLocation(`/command?q=${encodeURIComponent(command.trim())}`);
  };
  const isActive = (href: string) =>
    location === href ||
    location.startsWith(`${href}/`) ||
    (href === "/command" && location === "/command-center") ||
    (href === "/cmo" && location === "/marketing");

  useEffect(() => {
    if (!isLoading && !activeBusiness && location !== "/onboarding") {
      setLocation("/onboarding");
    }
  }, [activeBusiness, isLoading, location, setLocation]);

  if (!activeBusiness && isLoading) {
    return (
      <div className="empty full-screen-loading">
        <RefreshCw />
        <h3>Opening your business workspace</h3>
        <p>Loading your AI Business OS…</p>
      </div>
    );
  }
  if (!activeBusiness) return <>{children}</>;

  return (
    <div
      className={cx("app-shell", `theme-${legacyTheme}`)}
      data-business-theme={activeBusinessId}
      data-custom-brand={activeBusiness.brandIdentity ? "true" : "false"}
      style={brandThemeStyle(brandTheme)}
    >
      <aside className={cx("sidebar", open && "open")}>
        <div className="brand">
          <BusinessBrandMark
            businessName={activeBusiness.name}
            identity={activeBusiness.brandIdentity}
          />
          <div className="brand-text">
            <div className="brand-copy">{activeBusiness.name}</div>
            <div className="brand-sub">AI Business OS · workspace</div>
          </div>
        </div>
        <div className="nav-list">
          {navGroups.map((group) => (
            <div key={group.label}>
              <div className="nav-section">{group.label}</div>
              {filterBusinessFeatureItems(
                activeBusiness,
                group.items,
                billing?.entitlements ?? null,
              )
                .filter((item) => {
                  const module = NAV_MODULE_BY_HREF[item.href];
                  return !module || isWorkspaceModuleVisible(activeBusiness.industry, module);
                })
                .map((item) => {
                const Icon = item.icon;
                const module = NAV_MODULE_BY_HREF[item.href];
                const label =
                  module === "customers"
                    ? terminology.customerPlural
                    : module === "crm"
                      ? terminology.crmLabel
                      : module === "scheduling"
                        ? terminology.schedulingLabel
                        : module === "orders" &&
                            workspaceProfile.dashboardVariant === "real_estate"
                          ? "Deals & Viewings"
                          : item.label;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cx("nav-item", isActive(item.href) && "active")}
                    data-testid={`link-nav-${item.href.slice(1)}`}
                    onClick={() => setOpen(false)}
                  >
                    <Icon />
                    <span>{label}</span>
                  </Link>
                );
              })}
              {group.label === "Operations" && industryNav && IndustryIcon && (
                <Link
                  href={industryNav.href}
                  className={cx(
                    "nav-item",
                    isActive(industryNav.href) && "active",
                  )}
                  data-testid={`link-nav-${industryNav.href.slice(1)}`}
                  onClick={() => setOpen(false)}
                >
                  <IndustryIcon />
                  <span>{industryNav.label}</span>
                </Link>
              )}
            </div>
          ))}
        </div>
        <div className="sidebar-bottom">
          <Link
            href="/billing"
            className={cx("nav-item", location.startsWith("/billing") && "active")}
            data-testid="link-nav-billing"
          >
            <CreditCard />
            <span>Billing & plan</span>
            {billing && <small className="nav-plan-badge">{billing.plan_name}</small>}
          </Link>
          <Link
            href="/settings"
            className={cx("nav-item", location === "/settings" && "active")}
            data-testid="link-nav-settings"
          >
            <Settings2 />
            <span>Settings</span>
          </Link>
          <button
            className="profile profile-button"
            onClick={() => setProfileOpen(true)}
          >
            <Avatar name={displayName} />
            <div>
              <div className="profile-name">{displayName}</div>
              <div className="profile-role">
                {membershipRole} · {activeBusiness.name}
              </div>
            </div>
            <ChevronRight size={14} color="#aaa49c" />
          </button>
        </div>
      </aside>
      <main className="workspace">
        <header className="topbar">
          <button
            className="mobile-menu"
            onClick={() => setOpen((value) => !value)}
            aria-label="Toggle navigation"
            data-testid="button-toggle-navigation"
          >
            {open ? <PanelLeftClose /> : <Menu />}
          </button>
          <form className="global-search" onSubmit={runCommand}>
            <Search />
            <input
              value={command}
              onChange={(event) => setCommand(event.target.value)}
              placeholder="Ask your AI Business Manager..."
              aria-label="Ask your AI Business Manager"
              data-testid="input-global-command"
            />
          </form>
          <div
            className="business-select-wrap"
            style={{ position: "relative" }}
          >
            <button
              className="business-select"
              onClick={() => setBusinessOpen((value) => !value)}
              data-testid="button-business-selector"
            >
              <Globe2 />
              {activeBusiness.name}
              <ChevronDown size={13} />
            </button>
            {businessOpen && (
              <div
                className="card"
                style={{
                  position: "absolute",
                  top: 42,
                  right: 0,
                  width: 245,
                  zIndex: 30,
                  padding: 7,
                }}
              >
                {businesses.map((item) => (
                  <button
                    key={item.id}
                    className={cx(
                      "channel",
                      item.id === activeBusinessId && "active",
                    )}
                    onClick={() => {
                      selectBusiness(item.id);
                      setBusinessOpen(false);
                      notify(`Switched to ${item.name}`);
                    }}
                  >
                    <span className="business-menu-name">
                      <BusinessBrandMark
                        businessName={item.name}
                        identity={item.brandIdentity}
                      />
                      {item.name}
                    </span>
                    {item.id === activeBusinessId && <Check size={13} />}
                  </button>
                ))}
                <button
                  className="channel"
                  onClick={() => {
                    setBusinessOpen(false);
                    setLocation("/onboarding");
                  }}
                >
                  <Plus size={13} /> Add business
                </button>
              </div>
            )}
          </div>
          <div className="top-actions">
            <button
              className="icon-btn help-btn"
              aria-label="Help"
              data-testid="button-help"
              onClick={() => setHelpOpen(true)}
            >
              <CircleHelp />
            </button>
            <button
              className="icon-btn"
              aria-label="Notifications"
              data-testid="button-notifications"
              onClick={() => setNotificationsOpen(true)}
            >
              <Bell />
              {unreadNotifications > 0 && <i className="notif-dot" />}
            </button>
            <Avatar name={displayName} />
            <Button
              variant="primary"
              onClick={() => setLocation(canAutomate ? "/automations" : "/billing?feature=automations")}
              data-testid="button-new-automation"
            >
              {canAutomate ? <Plus /> : <CreditCard />} {canAutomate ? "New automation" : "View plan"}
            </Button>
          </div>
        </header>
        <div className="page" key={activeBusinessId}>
          {children}
        </div>
      </main>
      <Toasts
        items={toasts}
        remove={(id) =>
          setToasts((items) => items.filter((item) => item.id !== id))
        }
      />
      {notificationsOpen && (
        <NotificationCenter onClose={() => setNotificationsOpen(false)} />
      )}
      {helpOpen && (
        <Modal
          title="How can we help?"
          description="Jump to the part of your business that needs attention."
          onClose={() => setHelpOpen(false)}
        >
          <div className="help-grid">
            <button
              onClick={() => {
                setHelpOpen(false);
                setLocation("/command");
              }}
            >
              <Sparkles />
              <strong>Ask AI Manager</strong>
              <span>Understand performance or prepare an action.</span>
            </button>
            <button
              onClick={() => {
                setHelpOpen(false);
                setLocation("/reports/daily");
              }}
            >
              <FileText />
              <strong>Read daily report</strong>
              <span>See what your AI team completed and found.</span>
            </button>
            <button
              onClick={() => {
                setHelpOpen(false);
                setLocation("/brain");
              }}
            >
              <Brain />
              <strong>Review Business Brain</strong>
              <span>Improve the context behind AI decisions.</span>
            </button>
            <button
              onClick={() => {
                setHelpOpen(false);
                setLocation("/audit");
              }}
            >
              <ScrollText />
              <strong>Open audit history</strong>
              <span>Trace important human and AI activity.</span>
            </button>
          </div>
          <div className="prototype-note">
            Features awaiting dedicated provider APIs remain clearly separated
            from your authenticated business data.
          </div>
        </Modal>
      )}
      {profileOpen && (
        <Modal
          title={displayName}
          description={user?.email ?? "Owner profile"}
          onClose={() => setProfileOpen(false)}
        >
          <div className="profile-modal">
            <Avatar name={displayName} />
            <div>
              <h2>{activeBusiness.name}</h2>
              <p className="subtle">{membershipRole} · Authenticated access</p>
            </div>
          </div>
          <div className="modal-foot">
            <Button
              onClick={() => {
                setProfileOpen(false);
                setLocation("/settings");
              }}
            >
              <Settings2 /> Workspace settings
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                void logout().then(() => setLocation("/login"));
              }}
            >
              <LogOut /> Sign out
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
