import { useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  AlertCircle,
  BarChart3,
  CalendarDays,
  CheckCircle2,
  Package,
  Sparkles,
  Target,
  TrendingUp,
  Users,
} from "lucide-react";
import { useBusiness } from "@/business-context";
import {
  Badge,
  Button,
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
import { humanizeApiError } from "@/services/api-client";
import { operationsApi } from "@/services/operations";

export function DailyReportPage() {
  const {
    activeBusiness,
    activeBusinessId,
    billing,
  } = useBusiness();

  const client = useQueryClient();

  const [reportType, setReportType] = useState<
    "daily_operations" | "marketing"
  >("daily_operations");

  const workspaceProfile = getIndustryWorkspaceProfile(
    activeBusiness?.industry,
  );

  const terminology = workspaceProfile.terminology;

  const appointmentWorkspace =
    workspaceProfile.dashboardVariant === "healthcare" ||
    workspaceProfile.dashboardVariant ===
      "professional_services";

  const showOrders = isWorkspaceModuleVisible(
    activeBusiness?.industry,
    "orders",
  );

  const schedulingEnabled = isBusinessFeatureEnabled(
    activeBusiness,
    "scheduling",
    billing?.entitlements ?? null,
  );

  const reports = useQuery({
    queryKey: [
      "operations",
      activeBusinessId,
      "reports",
    ],
    queryFn: ({ signal }) =>
      operationsApi.reports.list(
        activeBusinessId,
        signal,
      ),
    enabled: Boolean(activeBusinessId),
  });

  const generate = useMutation({
    mutationFn: () => {
      const period = businessDateRange(
        activeBusiness?.timezone || "UTC",
        reportType === "marketing" ? 30 : 1,
      );

      return operationsApi.reports.generate(
        activeBusinessId,
        {
          report_type: reportType,
          period_start: period.start,
          period_end: period.end,
        },
      );
    },
    onSuccess: () =>
      void client.invalidateQueries({
        queryKey: [
          "operations",
          activeBusinessId,
          "reports",
        ],
      }),
  });

  const report = reports.data?.items[0];
  const metrics = report?.metrics;

  const money = (value: unknown) =>
    new Intl.NumberFormat(undefined, {
      style: "currency",
      currency:
        activeBusiness?.currency || "USD",
      notation: "compact",
    }).format(Number(value || 0));

  const leadLabel =
    workspaceProfile.dashboardVariant === "healthcare"
      ? "New inquiries"
      : "New leads";

  const operatingSummary = () => {
    if (!report) return "";

    if (appointmentWorkspace) {
      const customerPart =
        `${String(metrics?.customers ?? 0)} new ` +
        terminology.customerPlural.toLowerCase();

      if (!schedulingEnabled) {
        return `${customerPart} and ${String(
          metrics?.leads ?? 0,
        )} ${leadLabel.toLowerCase()}.`;
      }

      return (
        `${customerPart}, ` +
        `${String(metrics?.appointments ?? 0)} ` +
        `${terminology.bookingPlural.toLowerCase()}, and ` +
        `${String(metrics?.providers ?? 0)} ` +
        `${terminology.providerPlural.toLowerCase()}.`
      );
    }

    if (showOrders) {
      return (
        `${String(metrics?.orders ?? 0)} orders and ` +
        `${String(metrics?.customers ?? 0)} new ` +
        `${terminology.customerPlural.toLowerCase()}.`
      );
    }

    return (
      `${String(metrics?.customers ?? 0)} new ` +
      `${terminology.customerPlural.toLowerCase()} and ` +
      `${String(metrics?.leads ?? 0)} new leads.`
    );
  };

  const reportSummary =
    report?.report_type === "marketing"
      ? report.summary
      : operatingSummary();

  const operationsCardTitle =
    workspaceProfile.dashboardVariant ===
    "real_estate"
      ? "Deal activity"
      : "Sales";

  return (
    <>
      <PageHeader
        eyebrow="Daily AI Report"
        title={
          report
            ? "Your operating brief is ready"
            : "Generate your first operating brief"
        }
        subtitle={`A database-backed business report for ${
          activeBusiness?.name ||
          "the active business"
        }.`}
        action={
          <>
            <select
              className="business-select"
              value={reportType}
              onChange={(event) =>
                setReportType(
                  event.target.value as
                    | "daily_operations"
                    | "marketing",
                )
              }
            >
              <option value="daily_operations">
                Daily operations
              </option>
              <option value="marketing">
                Marketing performance
              </option>
            </select>

            <Button
              variant="primary"
              onClick={() => generate.mutate()}
              disabled={generate.isPending}
            >
              <Sparkles />
              {generate.isPending
                ? "Generating…"
                : "Generate report"}
            </Button>
          </>
        }
      />

      {reports.isError && (
        <Card>
          <div className="empty">
            <AlertCircle />
            <h3>Report unavailable</h3>
            <p>
              {humanizeApiError(
                reports.error,
                "Try again in a moment.",
              )}
            </p>
            <Button
              onClick={() =>
                void reports.refetch()
              }
            >
              Try again
            </Button>
          </div>
        </Card>
      )}

      {generate.isError && !reports.isError && (
        <div className="ai-banner">
          <AlertCircle />
          {humanizeApiError(generate.error, "The report could not be generated. Stored reports remain available.")}
        </div>
      )}

      {reports.isLoading && (
        <Card>
          <div className="empty">
            <p>Loading reports…</p>
          </div>
        </Card>
      )}

      {!reports.isLoading &&
        !report &&
        !reports.isError && (
          <Card>
            <div className="empty">
              <BarChart3 />
              <h3>No reports yet</h3>
              <p>
                Generate a report from current
                operational aggregates.
              </p>
            </div>
          </Card>
        )}

      {report && (
        <>
          <div className="report-summary">
            <Sparkles />

            <div>
              <div className="eyebrow">
                {report.report_type.replaceAll(
                  "_",
                  " ",
                )}{" "}
                ·{" "}
                {new Date(
                  report.generated_at,
                ).toLocaleString()}
              </div>

              <h2>{reportSummary}</h2>

              <p>
                Period: {report.period_start} through{" "}
                {report.period_end}. Values are
                generated from tenant-scoped database
                records.
              </p>
            </div>

            <Badge tone="success">
              <CheckCircle2 /> {report.status}
            </Badge>
          </div>

          <SectionTitle
            title={
              report.report_type === "marketing"
                ? "Marketing metrics"
                : "Operating metrics"
            }
          />

          {report.report_type === "marketing" ? (
            <div className="grid report-grid">
              <Card className="report-card">
                <div className="report-card-head">
                  <div className="integration-icon">
                    <TrendingUp />
                  </div>
                  <h2>Investment</h2>
                </div>

                <div className="stat-row">
                  <span>Spend</span>
                  <strong>
                    {money(metrics?.spend)}
                  </strong>
                </div>

                <div className="stat-row">
                  <span>Revenue</span>
                  <strong>
                    {money(metrics?.revenue)}
                  </strong>
                </div>

                <div className="stat-row">
                  <span>ROAS</span>
                  <strong>
                    {String(metrics?.roas ?? 0)}x
                  </strong>
                </div>
              </Card>

              <Card className="report-card">
                <div className="report-card-head">
                  <div className="integration-icon">
                    <Target />
                  </div>
                  <h2>Outcomes</h2>
                </div>

                <div className="stat-row">
                  <span>Leads</span>
                  <strong>
                    {String(metrics?.leads ?? 0)}
                  </strong>
                </div>

                <div className="stat-row">
                  <span>Conversions</span>
                  <strong>
                    {String(
                      metrics?.conversions ?? 0,
                    )}
                  </strong>
                </div>

                <div className="stat-row">
                  <span>Clicks</span>
                  <strong>
                    {String(metrics?.clicks ?? 0)}
                  </strong>
                </div>
              </Card>
            </div>
          ) : (
            <div className="grid report-grid">
              {showOrders && (
                <Card className="report-card">
                  <div className="report-card-head">
                    <div className="integration-icon">
                      <TrendingUp />
                    </div>
                    <h2>
                      {operationsCardTitle}
                    </h2>
                  </div>

                  <div className="stat-row">
                    <span>
                      {workspaceProfile.dashboardVariant ===
                      "real_estate"
                        ? "Recorded value"
                        : "Revenue"}
                    </span>
                    <strong>
                      {money(
                        metrics?.order_revenue,
                      )}
                    </strong>
                  </div>

                  <div className="stat-row">
                    <span>
                      {workspaceProfile.dashboardVariant ===
                      "real_estate"
                        ? "Deals / records"
                        : "Orders"}
                    </span>
                    <strong>
                      {String(
                        metrics?.orders ?? 0,
                      )}
                    </strong>
                  </div>

                  <div className="stat-row">
                    <span>
                      {workspaceProfile.dashboardVariant ===
                      "real_estate"
                        ? "Average value"
                        : "Average order"}
                    </span>
                    <strong>
                      {money(
                        metrics?.average_order_value,
                      )}
                    </strong>
                  </div>
                </Card>
              )}

              <Card className="report-card">
                <div className="report-card-head">
                  <div className="integration-icon">
                    <Users />
                  </div>
                  <h2>Relationships</h2>
                </div>

                <div className="stat-row">
                  <span>
                    New{" "}
                    {terminology.customerPlural.toLowerCase()}
                  </span>
                  <strong>
                    {String(
                      metrics?.customers ?? 0,
                    )}
                  </strong>
                </div>

                <div className="stat-row">
                  <span>{leadLabel}</span>
                  <strong>
                    {String(metrics?.leads ?? 0)}
                  </strong>
                </div>

                <div className="stat-row">
                  <span>Opportunities</span>
                  <strong>
                    {String(
                      metrics?.opportunities ?? 0,
                    )}
                  </strong>
                </div>
              </Card>

              {schedulingEnabled && (
                <Card className="report-card">
                  <div className="report-card-head">
                    <div className="integration-icon">
                      <CalendarDays />
                    </div>
                    <h2>
                      {terminology.schedulingLabel}
                    </h2>
                  </div>

                  <div className="stat-row">
                    <span>
                      {terminology.bookingPlural}
                    </span>
                    <strong>
                      {String(
                        metrics?.appointments ?? 0,
                      )}
                    </strong>
                  </div>

                  <div className="stat-row">
                    <span>
                      {terminology.providerPlural}
                    </span>
                    <strong>
                      {String(
                        metrics?.providers ?? 0,
                      )}
                    </strong>
                  </div>
                </Card>
              )}

              <Card className="report-card">
                <div className="report-card-head">
                  <div className="integration-icon">
                    <Target />
                  </div>
                  <h2>AI operations</h2>
                </div>

                <div className="stat-row">
                  <span>Executions</span>
                  <strong>
                    {String(
                      metrics?.ai_executions ?? 0,
                    )}
                  </strong>
                </div>

                <div className="stat-row">
                  <span>Actions</span>
                  <strong>
                    {String(
                      metrics?.ai_actions ?? 0,
                    )}
                  </strong>
                </div>
              </Card>
            </div>
          )}

          <Card>
            <SectionTitle
              title="Stored report record"
              action={<Package />}
            />
            <p className="subtle">
              Report ID {report.id}. The report is
              immutable output; generate another report
              to refresh the period.
            </p>
          </Card>
        </>
      )}
    </>
  );
}
