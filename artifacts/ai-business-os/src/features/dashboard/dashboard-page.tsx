import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowRight,
  CalendarDays,
  CheckCircle2,
  MessageCircle,
  Package,
  Sparkles,
  Target,
  TrendingUp,
  Users,
} from "lucide-react";
import { Link } from "wouter";
import { useBusiness } from "@/business-context";
import {
  Avatar,
  Badge,
  Card,
  PageHeader,
  SectionTitle,
} from "@/components/product-ui";
import { isBusinessFeatureEnabled } from "@/lib/business-features";
import {
  getIndustryWorkspaceProfile,
  isWorkspaceModuleVisible,
} from "@/lib/industry-workspaces";
import { businessDateRange } from "@/lib/operational-dates";
import { operationsApi } from "@/services/operations";

function Metric({
  title,
  value,
  foot,
  icon,
  tone,
}: {
  title: string;
  value: string;
  foot: string;
  icon: React.ReactNode;
  tone: string;
}) {
  return (
    <Card className="kpi">
      <div className="kpi-top">
        <span>{title}</span>
        <div className={`kpi-icon ${tone}`}>{icon}</div>
      </div>

      <div className="kpi-value">{value}</div>

      <div className="kpi-foot">
        <span>{foot}</span>
      </div>
    </Card>
  );
}

export function BusinessDashboardPage() {
  const {
    activeBusiness,
    activeBusinessId,
    billing,
  } = useBusiness();

  const workspaceProfile = getIndustryWorkspaceProfile(
    activeBusiness?.industry,
  );

  const terminology = workspaceProfile.terminology;

  const appointmentWorkspace =
    workspaceProfile.dashboardVariant === "healthcare" ||
    workspaceProfile.dashboardVariant === "professional_services";

  const showOrders = isWorkspaceModuleVisible(
    activeBusiness?.industry,
    "orders",
  );

  const schedulingEnabled = isBusinessFeatureEnabled(
    activeBusiness,
    "scheduling",
    billing?.entitlements ?? null,
  );

  const period = useMemo(
    () =>
      businessDateRange(
        activeBusiness?.timezone || "UTC",
        30,
      ),
    [activeBusiness?.timezone],
  );

  const analytics = useQuery({
    queryKey: [
      "operations",
      activeBusinessId,
      "analytics",
      period.start,
      period.end,
    ],
    queryFn: ({ signal }) =>
      operationsApi.analytics(
        activeBusinessId,
        period.start,
        period.end,
        signal,
      ),
    enabled: Boolean(activeBusinessId),
  });

  const orders = useQuery({
    queryKey: [
      "operations",
      activeBusinessId,
      "orders",
      "dashboard",
    ],
    queryFn: ({ signal }) =>
      operationsApi.orders.list(
        activeBusinessId,
        { pageSize: 5 },
        signal,
      ),
    enabled: Boolean(activeBusinessId && showOrders),
  });

  const conversations = useQuery({
    queryKey: [
      "operations",
      activeBusinessId,
      "conversations",
      "dashboard",
    ],
    queryFn: ({ signal }) =>
      operationsApi.conversations.list(
        activeBusinessId,
        { pageSize: 4 },
        signal,
      ),
    enabled: Boolean(activeBusinessId),
  });

  const opportunities = useQuery({
    queryKey: [
      "operations",
      activeBusinessId,
      "opportunities",
      "dashboard",
    ],
    queryFn: ({ signal }) =>
      operationsApi.opportunities.list(
        activeBusinessId,
        {
          status: "open",
          pageSize: 3,
        },
        signal,
      ),
    enabled: Boolean(activeBusinessId),
  });

  const a = analytics.data;

  const currency = activeBusiness?.currency || "USD";

  const money = (value: string | number) =>
    new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      notation: "compact",
    }).format(Number(value));

  const pendingOrders =
    orders.data?.items.filter(
      (item) =>
        !["completed", "canceled"].includes(item.status),
    ).length ?? 0;

  const loadFailed =
    analytics.isError ||
    conversations.isError ||
    opportunities.isError ||
    (showOrders && orders.isError);

  const businessActivity = appointmentWorkspace
    ? [
        {
          label: terminology.customerPlural,
          value: a?.customers ?? 0,
        },
        ...(schedulingEnabled
          ? [
              {
                label: terminology.providerPlural,
                value: a?.providers ?? 0,
              },
              {
                label: terminology.bookingPlural,
                value: a?.appointments ?? 0,
              },
            ]
          : []),
        {
          label: "Leads",
          value: a?.leads ?? 0,
        },
        {
          label: "AI actions",
          value: a?.ai_actions ?? 0,
        },
      ]
    : [
        ...(showOrders
          ? [
              {
                label: "Orders",
                value: a?.orders ?? 0,
              },
            ]
          : []),
        {
          label: terminology.customerPlural,
          value: a?.customers ?? 0,
        },
        {
          label: "Leads",
          value: a?.leads ?? 0,
        },
        ...(schedulingEnabled
          ? [
              {
                label: terminology.bookingPlural,
                value: a?.appointments ?? 0,
              },
            ]
          : []),
        {
          label: "AI actions",
          value: a?.ai_actions ?? 0,
        },
      ];

  const dashboardSubtitle = appointmentWorkspace
    ? `Live ${terminology.customerPlural.toLowerCase()}, ${terminology.providerPlural.toLowerCase()}, ${terminology.bookingPlural.toLowerCase()}, leads, conversations, and AI activity.`
    : "Here is the current operating picture from your business records.";

  return (
    <>
      <PageHeader
        eyebrow="Today · Live business data"
        title={`Good morning, ${activeBusiness?.name || "there"}`}
        subtitle={dashboardSubtitle}
        action={
          <Link
            href="/reports/daily"
            className="btn btn-secondary"
          >
            Daily report <ArrowRight />
          </Link>
        }
      />

      {loadFailed && (
        <Card>
          <div className="ai-banner">
            <Activity />
            Some dashboard data is temporarily unavailable.
            Individual workspaces can be retried.
          </div>
        </Card>
      )}

      <div className="grid kpi-grid">
        {appointmentWorkspace ? (
          <>
            <Metric
              title={terminology.customerPlural}
              value={String(a?.customers ?? 0)}
              foot="Current business records"
              icon={<Users />}
              tone="green"
            />

            <Metric
              title={terminology.providerPlural}
              value={String(
                schedulingEnabled
                  ? a?.providers ?? 0
                  : 0,
              )}
              foot={
                schedulingEnabled
                  ? `Available for ${terminology.bookingPlural.toLowerCase()}`
                  : "Scheduling is not included in the current plan"
              }
              icon={<Users />}
              tone="orange"
            />

            <Metric
              title={terminology.bookingPlural}
              value={String(
                schedulingEnabled
                  ? a?.appointments ?? 0
                  : 0,
              )}
              foot={
                schedulingEnabled
                  ? "Recorded in the current 30-day period"
                  : "Scheduling is not included in the current plan"
              }
              icon={<CalendarDays />}
              tone="brown"
            />

            <Metric
              title="New leads"
              value={String(a?.leads ?? 0)}
              foot="Created in 30 days"
              icon={<Target />}
              tone="rose"
            />
          </>
        ) : (
          <>
            <Metric
              title="30-day revenue"
              value={money(a?.order_revenue ?? 0)}
              foot={`${a?.orders ?? 0} non-canceled orders`}
              icon={<TrendingUp />}
              tone="green"
            />

            <Metric
              title="Pending orders"
              value={String(pendingOrders)}
              foot="Need operational attention"
              icon={<Package />}
              tone="orange"
            />

            <Metric
              title="Conversations"
              value={String(
                conversations.data?.total ?? 0,
              )}
              foot="Customer communication records"
              icon={<MessageCircle />}
              tone="brown"
            />

            <Metric
              title="New leads"
              value={String(a?.leads ?? 0)}
              foot="Created in 30 days"
              icon={<Target />}
              tone="rose"
            />
          </>
        )}
      </div>

      <div className="grid split-grid dashboard-primary">
        <Card className="health-card">
          <SectionTitle
            title={
              appointmentWorkspace
                ? `${workspaceProfile.dashboardVariant === "healthcare" ? "Care" : "Service"} activity`
                : "Business activity"
            }
            action={
              <Badge tone={a ? "success" : "neutral"}>
                {a ? "Live" : "Loading"}
              </Badge>
            }
          />

          <div className="health-bars">
            {businessActivity.map(({ label, value }) => (
              <div className="stat-row" key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
        </Card>

        <Card className="recommendation">
          <SectionTitle
            title="Open opportunities"
            action={
              <Link
                href="/opportunities"
                className="btn btn-sm btn-soft"
              >
                Review all <ArrowRight />
              </Link>
            }
          />

          {opportunities.data?.items.map((item) => (
            <div
              className="recommendation-item"
              key={item.id}
            >
              <div className="rec-icon">
                <Sparkles />
              </div>

              <div className="rec-title">
                {item.title}
              </div>

              <div className="rec-copy">
                {item.description}
              </div>

              <div className="rec-value">
                {item.estimated_value
                  ? `Estimated value · ${money(
                      item.estimated_value,
                    )}`
                  : `${item.priority} priority`}
              </div>
            </div>
          ))}

          {opportunities.data &&
            !opportunities.data.items.length && (
              <div className="empty compact-empty">
                <CheckCircle2 />
                <h3>No open opportunities</h3>
                <p>The opportunity queue is clear.</p>
              </div>
            )}
        </Card>
      </div>

      <div className="grid split-grid dashboard-activity-grid">
        <Card>
          <SectionTitle
            title="Recent conversations"
            action={
              <Link
                href="/conversations"
                className="subtle"
              >
                View inbox
              </Link>
            }
          />

          <div className="list">
            {conversations.data?.items.map((item) => (
              <div className="list-row" key={item.id}>
                <Avatar
                  name={
                    item.customer_display_name ||
                    item.channel
                  }
                />

                <div className="row-main">
                  <div className="row-title">
                    {item.customer_display_name ||
                      `Unmatched ${terminology.customerSingular.toLowerCase()}`}
                    <span className="row-copy inline-copy">
                      {" "}
                      · {item.channel}
                    </span>
                  </div>

                  <div className="row-copy">
                    {item.latest_message ||
                      "No message recorded"}
                  </div>
                </div>

                <div className="time">
                  {new Date(
                    item.last_activity_at,
                  ).toLocaleDateString()}
                </div>
              </div>
            ))}

            {conversations.data &&
              !conversations.data.items.length && (
                <div className="empty compact-empty">
                  <MessageCircle />
                  <p>No conversations yet.</p>
                </div>
              )}
          </div>
        </Card>

        <Card>
          <SectionTitle
            title={
              appointmentWorkspace
                ? "Scheduling coverage"
                : "System coverage"
            }
            action={
              appointmentWorkspace &&
              schedulingEnabled ? (
                <Link
                  href="/scheduling"
                  className="subtle"
                >
                  Open {terminology.schedulingLabel.toLowerCase()}
                </Link>
              ) : undefined
            }
          />

          <div className="list">
            {schedulingEnabled && (
              <div className="list-row">
                <Users />

                <div className="row-main">
                  <div className="row-title">
                    {a?.providers ?? 0}{" "}
                    {terminology.providerPlural.toLowerCase()}
                  </div>

                  <div className="row-copy">
                    {a?.appointments ?? 0}{" "}
                    {terminology.bookingPlural.toLowerCase()}{" "}
                    in this period
                  </div>
                </div>
              </div>
            )}

            <div className="list-row">
              <Activity />

              <div className="row-main">
                <div className="row-title">
                  {a?.ai_executions ?? 0} AI executions
                </div>

                <div className="row-copy">
                  {a?.ai_actions ?? 0} governed actions
                  recorded
                </div>
              </div>
            </div>
          </div>
        </Card>
      </div>

      {showOrders && (
        <>
          <SectionTitle
            title="Recent orders"
            action={
              <Link
                href="/orders"
                className="btn btn-sm btn-secondary"
              >
                View all <ArrowRight />
              </Link>
            }
          />

          <Card className="table-card" pad={false}>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Order</th>
                    <th>{terminology.customerSingular}</th>
                    <th>Items</th>
                    <th>Value</th>
                    <th>Status</th>
                    <th>Time</th>
                  </tr>
                </thead>

                <tbody>
                  {orders.data?.items.map((item) => (
                    <tr key={item.id}>
                      <td>
                        <strong>
                          {item.order_number}
                        </strong>
                      </td>

                      <td>
                        {item.customer_display_name}
                      </td>

                      <td>
                        {item.lines
                          .map(
                            (line) =>
                              `${line.description} × ${line.quantity}`,
                          )
                          .join(", ")}
                      </td>

                      <td>
                        <strong>
                          {money(item.total)}
                        </strong>
                      </td>

                      <td>
                        <Badge
                          tone={
                            item.status === "completed"
                              ? "success"
                              : item.status === "canceled"
                                ? "neutral"
                                : "warning"
                          }
                        >
                          {item.status}
                        </Badge>
                      </td>

                      <td>
                        {new Date(
                          item.created_at,
                        ).toLocaleDateString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {orders.data &&
                !orders.data.items.length && (
                  <div className="empty">
                    <Package />
                    <h3>No orders yet</h3>
                    <p>
                      Create an order to populate your
                      operating dashboard.
                    </p>
                  </div>
                )}
            </div>
          </Card>
        </>
      )}
    </>
  );
}
