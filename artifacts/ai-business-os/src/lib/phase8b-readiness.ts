export type ProviderReadinessDefinition = {
  setup_status: "available" | "provider_setup_required" | "coming_soon";
  webhook_support: boolean;
  external_writes_enabled: boolean;
  resource_selection_required: boolean;
};

export type ProviderReadinessConnection = {
  status: string;
  authentication_state: string;
  health: string;
  last_health_check_at: string | null;
  selected_resources: readonly unknown[];
};

export type ProviderReadinessBadge = {
  id: "configuration" | "authentication" | "read" | "webhook" | "write" | "production";
  label: string;
  tone: "success" | "warning" | "danger" | "neutral" | "info";
};

/**
 * Convert server evidence into owner-facing labels without treating OAuth as
 * read, webhook, write, or production acceptance.
 */
export function providerReadinessBadges(
  definition: ProviderReadinessDefinition,
  connection: ProviderReadinessConnection | null | undefined,
  options: { webhookAccepted?: boolean } = {},
): ProviderReadinessBadge[] {
  const codeReady = definition.setup_status === "available";
  const authenticated = Boolean(
    connection &&
      ["connected", "degraded"].includes(connection.status) &&
      connection.authentication_state === "authorized",
  );
  const readVerified = Boolean(
    authenticated &&
      connection?.status === "connected" &&
      connection.health === "healthy" &&
      connection.last_health_check_at,
  );
  const resourcesReady = Boolean(
    !definition.resource_selection_required || connection?.selected_resources.length,
  );
  const writeConfigured = Boolean(
    codeReady &&
      readVerified &&
      resourcesReady &&
      definition.external_writes_enabled,
  );

  const configuration: ProviderReadinessBadge =
    definition.setup_status === "coming_soon"
      ? { id: "configuration", label: "Provider approval required", tone: "warning" }
      : codeReady
        ? { id: "configuration", label: "Code ready", tone: "info" }
        : { id: "configuration", label: "Not configured", tone: "neutral" };

  return [
    configuration,
    authenticated
      ? { id: "authentication", label: "Authenticated", tone: "success" }
      : { id: "authentication", label: "Authentication required", tone: "warning" },
    readVerified
      ? { id: "read", label: "Read verified", tone: "success" }
      : { id: "read", label: "Read not verified", tone: "neutral" },
    ...(definition.webhook_support
      ? [
          options.webhookAccepted
            ? ({ id: "webhook", label: "Webhook verified", tone: "success" } as const)
            : ({ id: "webhook", label: "Webhook not verified", tone: "neutral" } as const),
        ]
      : []),
    writeConfigured
      ? { id: "write", label: "Write configured · approval required", tone: "warning" }
      : { id: "write", label: "Write unavailable", tone: "neutral" },
    {
      id: "production",
      label: "Production acceptance not recorded",
      tone: "neutral",
    },
  ];
}
