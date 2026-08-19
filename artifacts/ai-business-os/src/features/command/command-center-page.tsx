import { useEffect, useState } from "react";
import { useLocation } from "wouter";
import { ArrowRight, Bot, Check, Database, Send, Sparkles } from "lucide-react";
import { useBusiness } from "@/business-context";
import { Badge, Button, Card, PageHeader } from "@/components/product-ui";
import { useWorkspaceData } from "@/hooks/use-workspace-data";

type ManagerResponse = {
  answer: string;
  data: string[];
  reason: string;
  action: string;
};

export function CommandCenterPage() {
  const [, setLocation] = useLocation();
  const { activeBusiness } = useBusiness();
  const { data } = useWorkspaceData();
  const initialQuery =
    new URLSearchParams(window.location.search).get("q") ?? "";
  const [query, setQuery] = useState(initialQuery);
  const [response, setResponse] = useState<ManagerResponse | null>(null);
  const examples = [
    "How is my business doing?",
    "What should I focus on today?",
    "Why did sales decrease?",
    "Find inactive customers",
    "Create a marketing campaign",
    "Show today’s orders",
    "Follow up with my high-value leads",
  ];

  const ask = (text: string) => {
    setQuery(text);
    const lower = text.toLowerCase();
    const isRealEstate = activeBusiness?.industry === "Real Estate";
    const primaryOffer =
      activeBusiness?.products[0]?.name ??
      (isRealEstate ? "Oak Hills Home" : "Fresh Eggs");
    const pendingOrders = data.orders.filter(
      (order) => !["Delivered", "Closed", "Completed"].includes(order.status),
    );
    const highIntentLeads = data.leads.filter((lead) => lead.score >= 80);
    const atRiskCustomers = data.customers.filter((customer) =>
      customer.segment.toLowerCase().includes("risk"),
    );

    if (lower.includes("inactive") || lower.includes("campaign")) {
      setResponse({
        answer: isRealEstate
          ? `${highIntentLeads.length} qualified buyer leads are ready for focused follow-up. ${primaryOffer} is generating strong intent, so a focused viewing campaign is timely.`
          : `${Math.max(atRiskCustomers.length, 1)} customer segment is ready for reactivation. ${primaryOffer} is a strong repeat offer, and a focused message would be timely.`,
        data: [
          `Customer history · ${data.customers.length} profiles`,
          `Orders · ${data.orders.length} business records`,
          `Product performance · ${primaryOffer}`,
        ],
        reason: isRealEstate
          ? "These leads have strong intent signals and a clear next step. A focused viewing invitation is lower risk than a broad campaign."
          : "These customers have a strong prior purchase pattern but their order cadence has paused. A focused message is lower risk than a broad discount.",
        action: "Create a reactivation campaign",
      });
      return;
    }

    if (lower.includes("order")) {
      const leadingOrder = pendingOrders[0] ?? data.orders[0];
      setResponse({
        answer: `There are ${pendingOrders.length} pending ${isRealEstate ? "deals or viewings" : "orders"}. ${data.approvals.filter((item) => item.status === "Pending").length} are waiting for an owner decision${leadingOrder ? `, while ${leadingOrder.id} is ${leadingOrder.status.toLowerCase()}` : ""}.`,
        data: [
          `${isRealEstate ? "Deals & viewings" : "Orders"} · Current workspace`,
          `Operations activity · ${pendingOrders.length} pending`,
          leadingOrder
            ? `Latest status · ${leadingOrder.id}`
            : "Latest status · No active records",
        ],
        reason:
          "The current queue is healthy, but pending owner actions could slow the next operational step.",
        action: "Review pending orders",
      });
      return;
    }

    const strongestPublishedPost = data.socialPosts
      .filter((post) => post.status === "Published")
      .sort((left, right) => right.engagement - left.engagement)[0];
    setResponse({
      answer: `${activeBusiness?.name ?? "Your business"} has ${data.analytics.orders} ${isRealEstate ? "active deal events" : "orders"} in the current analytics view. ${primaryOffer} is a leading offer, and your AI team has ${data.approvals.filter((item) => item.status === "Pending").length} actions ready for review.`,
      data: [
        `Revenue · ${data.analytics.revenue.toLocaleString()}`,
        `Product mix · ${primaryOffer}`,
        strongestPublishedPost
          ? `Marketing · ${strongestPublishedPost.engagement}% engagement`
          : `Customers · ${data.analytics.customers} active profiles`,
      ],
      reason:
        "The recommendation is grounded in this business workspace’s current operations, customers, marketing, and approval queue.",
      action: "View business opportunities",
    });
  };

  useEffect(() => {
    if (initialQuery) ask(initialQuery);
    // The URL query is intentionally consumed only on first entry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      <PageHeader
        eyebrow="Business Manager"
        title="AI Command Center"
        subtitle="Ask anything about your business or give your AI team a task."
      />
      <Card className="card-pad" style={{ maxWidth: 930 }}>
        <div
          style={{
            display: "flex",
            gap: 10,
            alignItems: "center",
            border: "1px solid #ddd8d1",
            padding: "6px 7px 6px 13px",
            borderRadius: 9,
            background: "#fcfbfa",
          }}
        >
          <Sparkles size={17} color="#16803d" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") ask(query);
            }}
            placeholder="Ask about revenue, customers, orders, or give your AI team a task..."
            style={{
              border: 0,
              outline: 0,
              flex: 1,
              background: "transparent",
              fontSize: 12,
            }}
            data-testid="input-command-center"
          />
          <Button
            variant="green"
            onClick={() => ask(query)}
            data-testid="button-send-command"
          >
            <Send />
          </Button>
        </div>
        <div className="eyebrow" style={{ marginTop: 20, marginBottom: 10 }}>
          Try asking
        </div>
        <div className="filters">
          {examples.map((example) => (
            <button
              key={example}
              className="filter active"
              onClick={() => ask(example)}
              data-testid={`button-example-${example.slice(0, 8).replaceAll(" ", "-")}`}
            >
              {example}
            </button>
          ))}
        </div>
      </Card>
      {response ? (
        <Card className="card-pad" style={{ maxWidth: 930, marginTop: 15 }}>
          <div className="ai-banner">
            <Bot />
            Manager response · grounded in your business data
          </div>
          <div className="eyebrow">1 · Understanding</div>
          <h2 style={{ fontSize: 18, maxWidth: 720, lineHeight: 1.45 }}>
            {response.answer}
          </h2>
          <div className="grid split-grid" style={{ marginTop: 20 }}>
            <div>
              <div className="eyebrow">2 · Data used</div>
              {response.data.map((item) => (
                <div className="list-row" key={item}>
                  <Database size={14} color="#4b9e61" />
                  <div className="row-title">{item}</div>
                  <Check size={14} color="#4b9e61" />
                </div>
              ))}
            </div>
            <div>
              <div className="eyebrow">3 · Reasoning</div>
              <p className="subtle" style={{ lineHeight: 1.7 }}>
                {response.reason}
              </p>
              <div className="eyebrow" style={{ marginTop: 18 }}>
                4 · Recommendation
              </div>
              <div
                className="card"
                style={{
                  padding: 12,
                  background: "#fff9f1",
                  borderColor: "#f6e1c9",
                }}
              >
                <div className="eyebrow">5 · Proposed action</div>
                <div className="row-title">{response.action}</div>
                <p className="row-copy">
                  This action will be staged for your review before anything is
                  sent.
                </p>
                <div className="command-approval">
                  <Badge tone="warning">6 · Approval required</Badge>
                  <span>
                    Owner review protects external messages and campaign spend.
                  </span>
                </div>
                <div className="toolbar" style={{ marginTop: 10 }}>
                  <Button
                    variant="primary"
                    className="btn-sm"
                    onClick={() =>
                      setLocation(
                        response.action.includes("campaign")
                          ? "/campaigns?new=1"
                          : response.action.includes("orders")
                            ? "/orders"
                            : "/opportunities",
                      )
                    }
                    data-testid="button-execute-recommendation"
                  >
                    7 · Review result <ArrowRight />
                  </Button>
                  <Button
                    variant="secondary"
                    className="btn-sm"
                    onClick={() => setResponse(null)}
                    data-testid="button-clear-response"
                  >
                    Clear
                  </Button>
                </div>
              </div>
            </div>
          </div>
        </Card>
      ) : (
        <div className="empty" style={{ maxWidth: 930, marginTop: 28 }}>
          <Sparkles />
          <h3>Your business context is ready</h3>
          <p>
            Ask a question to see what happened, why it happened, and what your
            AI team recommends next.
          </p>
        </div>
      )}
    </>
  );
}
