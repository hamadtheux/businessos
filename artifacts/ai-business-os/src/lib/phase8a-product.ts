export type AutopilotPackSupport = "preparation_supported" | "setup_required";

export type AutopilotPack = {
  id: string;
  title: string;
  outcome: string;
  support: AutopilotPackSupport;
  trigger: string;
  prompt: string | null;
  connectionOptions: string[];
  setupReason: string | null;
};

/**
 * Business-language entry points for the existing workflow compiler. A pack is
 * marked preparation_supported only when the compiler has a deterministic
 * trigger for it. Compilation prepares a reviewable workflow; it never claims
 * that an external provider action has already happened.
 */
export const AUTOPILOT_PACKS: readonly AutopilotPack[] = [
  {
    id: "order-confirmation",
    title: "Order confirmation",
    outcome: "Prepare a confirmation when a new order is recorded.",
    support: "preparation_supported",
    trigger: "New order",
    prompt:
      "When an order is created, prepare a confirmation email for governed review and stop after the message decision is recorded.",
    connectionOptions: ["gmail", "microsoft_outlook"],
    setupReason: null,
  },
  {
    id: "shipping-update",
    title: "Shipping update",
    outcome: "Notify a customer when tracking or shipment state changes.",
    support: "setup_required",
    trigger: "Shipment update",
    prompt: null,
    connectionOptions: ["gmail", "microsoft_outlook", "whatsapp_business"],
    setupReason:
      "Shipment-change events are not yet a supported Copilot trigger.",
  },
  {
    id: "delivery-follow-up",
    title: "Delivery follow-up",
    outcome: "Prepare a helpful follow-up after delivery is recorded.",
    support: "preparation_supported",
    trigger: "Order delivered",
    prompt:
      "When an order is delivered, wait 2 days, then prepare a customer email for governed review.",
    connectionOptions: ["gmail", "microsoft_outlook"],
    setupReason: null,
  },
  {
    id: "review-request",
    title: "Review request",
    outcome: "Prepare a review request after a completed delivery.",
    support: "preparation_supported",
    trigger: "Order delivered",
    prompt:
      "When an order is delivered, wait 2 days, then ask for a review by email if allowed.",
    connectionOptions: ["gmail", "microsoft_outlook"],
    setupReason: null,
  },
  {
    id: "abandoned-checkout",
    title: "Abandoned checkout",
    outcome: "Prepare recovery outreach and stop immediately after purchase.",
    support: "setup_required",
    trigger: "Checkout abandoned",
    prompt: null,
    connectionOptions: ["whatsapp_business"],
    setupReason:
      "Checkout events do not yet guarantee a trusted customer conversation and WhatsApp service window.",
  },
  {
    id: "new-lead-response",
    title: "New lead response",
    outcome: "Prepare a prompt first response for every new lead.",
    support: "preparation_supported",
    trigger: "New lead",
    prompt:
      "When a lead is created, prepare a customer email for governed review.",
    connectionOptions: ["gmail", "microsoft_outlook"],
    setupReason: null,
  },
  {
    id: "lead-follow-up",
    title: "Lead follow-up",
    outcome: "Prepare a follow-up after a new lead has had time to respond.",
    support: "preparation_supported",
    trigger: "New lead",
    prompt:
      "When a lead is created, wait 1 day, then prepare a customer email for governed review.",
    connectionOptions: ["gmail", "microsoft_outlook"],
    setupReason: null,
  },
  {
    id: "customer-reactivation",
    title: "Inactive customer reactivation",
    outcome: "Identify and prepare outreach for genuinely inactive customers.",
    support: "setup_required",
    trigger: "Customer inactivity",
    prompt: null,
    connectionOptions: ["gmail", "microsoft_outlook", "whatsapp_business"],
    setupReason:
      "A tenant-defined inactivity rule and trusted activity signal are required.",
  },
  {
    id: "support-escalation",
    title: "Support escalation",
    outcome: "Escalate urgent or unresolved customer conversations.",
    support: "setup_required",
    trigger: "Support risk detected",
    prompt: null,
    connectionOptions: ["gmail", "microsoft_outlook", "whatsapp_business"],
    setupReason:
      "A deterministic escalation trigger is not yet available to Copilot.",
  },
  {
    id: "social-recommendation",
    title: "Social recommendation",
    outcome: "Prepare a grounded content recommendation for owner review.",
    support: "setup_required",
    trigger: "Content opportunity",
    prompt: null,
    connectionOptions: ["instagram", "facebook"],
    setupReason:
      "Use AI CMO content planning; workflow compilation does not invent social signals.",
  },
] as const;

export type ConnectionHealthInput = {
  connector_type: string;
  status: string;
  authentication_state: string;
  health: string;
};

export function hasHealthyConnection(
  connections: readonly ConnectionHealthInput[],
  connectorOptions: readonly string[],
) {
  return connections.some(
    (connection) =>
      connectorOptions.includes(connection.connector_type) &&
      connection.status === "connected" &&
      connection.authentication_state === "authorized" &&
      connection.health === "healthy",
  );
}

export type ConnectorWriteReadinessInput = {
  connector_type: string;
  external_writes_enabled: boolean;
  setup_status: string;
};

export function hasWriteReadyConnection(
  connections: readonly ConnectionHealthInput[],
  registry: readonly ConnectorWriteReadinessInput[],
  connectorOptions: readonly string[],
) {
  const healthyConnectorTypes = new Set(
    connections
      .filter(
        (connection) =>
          connectorOptions.includes(connection.connector_type) &&
          connection.status === "connected" &&
          connection.authentication_state === "authorized" &&
          connection.health === "healthy",
      )
      .map((connection) => connection.connector_type),
  );
  return registry.some(
    (connector) =>
      healthyConnectorTypes.has(connector.connector_type) &&
      connector.external_writes_enabled &&
      connector.setup_status === "available",
  );
}

export type ReadinessState = "ready" | "action_needed" | "unavailable" | "checking";

export type ReadinessItem = {
  id: string;
  label: string;
  detail: string;
  href: string;
  state: ReadinessState;
};

export type FirstClientReadinessInput = {
  profileReady: boolean;
  brainSourceCount: number | null | undefined;
  catalogApplicable: boolean;
  catalogItemCount: number | null | undefined;
  enabledAgentCount: number | null | undefined;
  activeWorkflowCount: number | null | undefined;
  healthyCommunicationConnection: boolean | null | undefined;
  healthySocialConnection: boolean | null | undefined;
  commerceApplicable: boolean;
  healthyCommerceConnection: boolean | null | undefined;
  brandingReady: boolean;
  processingHealthy: boolean | null | undefined;
};

function measuredItem(
  id: string,
  label: string,
  href: string,
  value: boolean | null | undefined,
  readyDetail: string,
  actionDetail: string,
): ReadinessItem {
  return {
    id,
    label,
    href,
    state:
      value === undefined
        ? "checking"
        : value === null
          ? "unavailable"
          : value
            ? "ready"
            : "action_needed",
    detail:
      value === undefined
        ? "Checking live status…"
        : value === null
          ? "Status could not be verified."
          : value
            ? readyDetail
            : actionDetail,
  };
}

export function buildFirstClientReadiness(
  input: FirstClientReadinessInput,
): ReadinessItem[] {
  const items: ReadinessItem[] = [
    measuredItem(
      "profile",
      "Business profile",
      "/settings",
      input.profileReady,
      "Core business facts and voice are saved.",
      "Add a business description and brand voice.",
    ),
    measuredItem(
      "brain",
      "Business Brain",
      "/business-brain",
      input.brainSourceCount == null ? input.brainSourceCount : input.brainSourceCount > 0,
      `${input.brainSourceCount ?? 0} trusted source(s) available.`,
      "Add trusted knowledge before relying on AI output.",
    ),
  ];

  if (input.catalogApplicable) {
    items.push(
      measuredItem(
        "catalog",
        "Products & services",
        "/catalog",
        input.catalogItemCount == null ? input.catalogItemCount : input.catalogItemCount > 0,
        `${input.catalogItemCount ?? 0} catalog item(s) available.`,
        "Add or import the products and services you sell.",
      ),
    );
  }

  items.push(
    measuredItem(
      "agents",
      "AI team",
      "/agents",
      input.enabledAgentCount == null ? input.enabledAgentCount : input.enabledAgentCount > 0,
      `${input.enabledAgentCount ?? 0} AI employee(s) enabled.`,
      "Enable and review at least one AI employee.",
    ),
    measuredItem(
      "automations",
      "Business Autopilot",
      "/automations",
      input.activeWorkflowCount == null ? input.activeWorkflowCount : input.activeWorkflowCount > 0,
      `${input.activeWorkflowCount ?? 0} workflow(s) active.`,
      "Prepare, test, and activate a first workflow.",
    ),
    measuredItem(
      "communication",
      "Customer communication",
      "/integrations",
      input.healthyCommunicationConnection,
      "A customer communication provider is authenticated, healthy, and write-capable.",
      "Connect Gmail, Outlook, or WhatsApp Business and complete provider write setup.",
    ),
    measuredItem(
      "social",
      "Social channel",
      "/integrations",
      input.healthySocialConnection,
      "A social provider is authenticated, healthy, and write-capable.",
      "Connect Instagram or Facebook and complete publishing approval.",
    ),
  );

  if (input.commerceApplicable) {
    items.push(
      measuredItem(
        "commerce",
        "Commerce data",
        "/commerce",
        input.healthyCommerceConnection,
        "A commerce source is connected and healthy.",
        "Connect or import a real commerce source.",
      ),
    );
  }

  items.push(
    measuredItem(
      "branding",
      "Brand identity",
      "/settings",
      input.brandingReady,
      "Saved brand identity is available.",
      "Add a logo or brand colors.",
    ),
    measuredItem(
      "processing",
      "Durable processing",
      "/automations",
      input.processingHealthy,
      "Worker and scheduler processing is healthy.",
      "Restore worker and scheduler health before activation.",
    ),
  );

  return items;
}

export function readinessCounts(items: readonly ReadinessItem[]) {
  return {
    ready: items.filter((item) => item.state === "ready").length,
    actionNeeded: items.filter((item) => item.state === "action_needed").length,
    unavailable: items.filter((item) => item.state === "unavailable").length,
    checking: items.filter((item) => item.state === "checking").length,
    total: items.length,
  };
}

export function isTodayInTimezone(
  value: string,
  timezone: string,
  now = new Date(),
) {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return formatter.format(new Date(value)) === formatter.format(now);
}
