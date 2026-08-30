import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  BarChart3,
  Calendar,
  Check,
  Clock3,
  Database,
  Facebook,
  Globe2,
  Link2,
  Mail,
  MessageCircle,
  RefreshCw,
  ShieldCheck,
  Unplug,
} from "lucide-react";
import { useBusiness } from "@/business-context";
import { PRODUCT_NAME } from "@/config/brand";
import {
  Badge,
  Button,
  Card,
  Modal,
  PageHeader,
  SectionTitle,
} from "@/components/product-ui";
import { recommendedIntegrationConnectors } from "@/lib/business-features";
import { providerReadinessBadges } from "@/lib/phase8b-readiness";
import { humanizeApiError } from "@/services/api-client";
import {
  integrationsApi,
  type ConnectorDefinition,
  type ExternalIntegrationResource,
  type IntegrationConnection,
  type IntegrationConnectorType,
} from "@/services/integrations";

const icons: Record<IntegrationConnectorType, typeof Link2> = {
  whatsapp_business: MessageCircle,
  gmail: Mail,
  google_calendar: Calendar,
  google_ads: BarChart3,
  meta_ads: BarChart3,
  facebook: Facebook,
  instagram: Globe2,
  microsoft_outlook: Mail,
};

const connectedStatuses = new Set<IntegrationConnection["status"]>([
  "connected",
  "degraded",
]);
const humanize = (value: string) => value.replaceAll("_", " ");
const formatDate = (value: string | null) =>
  value
    ? new Date(value).toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "Not yet";

function connectionTone(status: IntegrationConnection["status"] | undefined) {
  if (status === "connected") return "success";
  if (
    status === "pending" ||
    status === "degraded" ||
    status === "reauth_required"
  )
    return "warning";
  if (status === "revoked") return "danger";
  return "neutral";
}

function setupLabel(definition: ConnectorDefinition) {
  if (definition.setup_status === "coming_soon")
    return "Connector integration required";
  if (definition.setup_status === "provider_setup_required")
    return "Provider setup required";
  return "Available to connect";
}

export function IntegrationsPage() {
  const { activeBusinessId, activeBusiness } = useBusiness();
  const queryClient = useQueryClient();
  const [selectedType, setSelectedType] =
    useState<IntegrationConnectorType | null>(null);
  const [connectTarget, setConnectTarget] =
    useState<ConnectorDefinition | null>(null);
  const [disconnectTarget, setDisconnectTarget] =
    useState<IntegrationConnection | null>(null);
  const [resourceKey, setResourceKey] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setSelectedType(null);
    setConnectTarget(null);
    setDisconnectTarget(null);
    setResourceKey("");
    setError("");
  }, [activeBusinessId]);

  const registry = useQuery({
    queryKey: ["integrations", activeBusinessId, "registry"],
    queryFn: ({ signal }) => integrationsApi.registry(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
  });
  const connections = useQuery({
    queryKey: ["integrations", activeBusinessId, "connections"],
    queryFn: ({ signal }) =>
      integrationsApi.connections(activeBusinessId, signal),
    enabled: Boolean(activeBusinessId),
  });

  const recommended = useMemo(
    () => recommendedIntegrationConnectors(activeBusiness?.industry),
    [activeBusiness?.industry],
  );
  const recommendedSet = useMemo(() => new Set(recommended), [recommended]);
  const definitions = useMemo(() => {
    const rank = new Map(recommended.map((item, index) => [item, index]));
    return [...(registry.data ?? [])].sort((left, right) => {
      const leftRank = rank.get(left.connector_type) ?? 100;
      const rightRank = rank.get(right.connector_type) ?? 100;
      return (
        leftRank - rightRank ||
        left.display_name.localeCompare(right.display_name)
      );
    });
  }, [recommended, registry.data]);
  const byType = useMemo(
    () =>
      new Map(
        (connections.data ?? []).map((item) => [item.connector_type, item]),
      ),
    [connections.data],
  );
  const selectedDefinition =
    definitions.find((item) => item.connector_type === selectedType) ?? null;
  const selectedConnection = selectedType
    ? (byType.get(selectedType) ?? null)
    : null;
  const canReadProvider = Boolean(
    selectedConnection &&
    connectedStatuses.has(selectedConnection.status) &&
    selectedConnection.authentication_state === "authorized",
  );
  const resources = useQuery({
    queryKey: [
      "integrations",
      activeBusinessId,
      "resources",
      selectedConnection?.id,
    ],
    queryFn: ({ signal }) =>
      integrationsApi.resources(
        activeBusinessId,
        selectedConnection!.id,
        signal,
      ),
    enabled: Boolean(
      activeBusinessId && selectedConnection?.id && canReadProvider,
    ),
    retry: false,
  });
  const events = useQuery({
    queryKey: [
      "integrations",
      activeBusinessId,
      "events",
      selectedConnection?.id,
    ],
    queryFn: ({ signal }) =>
      integrationsApi.events(activeBusinessId, selectedConnection!.id, signal),
    enabled: Boolean(activeBusinessId && selectedConnection?.id),
  });

  useEffect(() => {
    if (!resources.data?.length) {
      setResourceKey("");
      return;
    }
    setResourceKey((current) => current || resourceValue(resources.data[0]));
  }, [resources.data]);

  const invalidate = async () => {
    await queryClient.invalidateQueries({
      queryKey: ["integrations", activeBusinessId],
    });
  };
  const fail = (reason: unknown, fallback: string) =>
    setError(humanizeApiError(reason, fallback));
  const authorize = useMutation({
    mutationFn: (target: {
      definition: ConnectorDefinition;
      connection?: IntegrationConnection | null;
    }) =>
      target.connection
        ? integrationsApi.reconnect(activeBusinessId, target.connection.id)
        : integrationsApi.authorize(
            activeBusinessId,
            target.definition.connector_type,
          ),
    onSuccess: (result) => {
      setError("");
      const target = new URL(
        result.authorization_url,
        globalThis.location.origin,
      );
      if (!new Set(["https:", "http:"]).has(target.protocol)) {
        setError("The provider returned an invalid authorization address.");
        return;
      }
      globalThis.location.assign(target.toString());
    },
    onError: (reason) =>
      fail(
        reason,
        "Authorization could not be started. Provider setup may still be required.",
      ),
  });
  const health = useMutation({
    mutationFn: (connection: IntegrationConnection) =>
      integrationsApi.health(activeBusinessId, connection.id),
    onSuccess: () => {
      setError("");
      void invalidate();
    },
    onError: (reason) =>
      fail(
        reason,
        "The read-only provider health check could not be completed.",
      ),
  });
  const disconnect = useMutation({
    mutationFn: (connection: IntegrationConnection) =>
      integrationsApi.disconnect(activeBusinessId, connection.id),
    onSuccess: () => {
      setDisconnectTarget(null);
      setError("");
      void invalidate();
    },
    onError: (reason) => fail(reason, "The connection could not be disabled."),
  });
  const selectResource = useMutation({
    mutationFn: (resource: ExternalIntegrationResource) =>
      integrationsApi.selectResource(
        activeBusinessId,
        selectedConnection!.id,
        resource,
      ),
    onSuccess: () => {
      setError("");
      void invalidate();
    },
    onError: (reason) =>
      fail(reason, "The resource selection could not be saved."),
  });

  const loadError = registry.error || connections.error;
  const activeCount = (connections.data ?? []).filter((item) =>
    connectedStatuses.has(item.status),
  ).length;
  const externalWritesEnabled = definitions.some(
    (item) => item.external_writes_enabled,
  );
  return (
    <>
      <PageHeader
        eyebrow="Workspace connections"
        title="Integrations"
        subtitle={
          externalWritesEnabled
            ? "Connect approved business accounts, select bounded resources, and route writes through policy, approval, spend controls, and the durable dispatcher."
            : `Connect approved business accounts, choose the resources ${PRODUCT_NAME} may read, and monitor connection health. Provider writes remain disabled until platform configuration is complete.`
        }
        action={
          <>
            <Badge tone="success">
              <ShieldCheck /> Server-managed authorization
            </Badge>
            <Badge tone="neutral">{activeCount} active</Badge>
          </>
        }
      />
      {loadError && (
        <Card>
          <div className="empty">
            <AlertCircle />
            <h3>Integrations are unavailable</h3>
            <p>{humanizeApiError(loadError, "Try again in a moment.")}</p>
            <Button
              onClick={() => {
                void registry.refetch();
                void connections.refetch();
              }}
            >
              Try again
            </Button>
          </div>
        </Card>
      )}
      {!loadError && (registry.isLoading || connections.isLoading) && (
        <Card>
          <div className="empty">
            <RefreshCw className="spin" />
            <p>Loading the connector registry…</p>
          </div>
        </Card>
      )}
      {!loadError && !registry.isLoading && !connections.isLoading && (
        <>
          <div className="integration-foundation-note">
            <ShieldCheck />
            <div>
              <strong>
                {externalWritesEnabled
                  ? "Governed connector execution"
                  : "Provider configuration boundary"}
              </strong>
              <p>
                {externalWritesEnabled
                  ? "Enabled provider writes still require a tenant-owned connection, selected resource, matching capability, policy, approval, spend authorization, and a durable attempt."
                  : "OAuth, callbacks, secure token storage, webhooks, resource selection, and dispatch adapters are code-ready. No connector can write until the platform enables secure provider configuration."}
              </p>
            </div>
          </div>
          {!definitions.length && (
            <Card>
              <div className="empty">
                <Link2 />
                <h3>No connectors are available</h3>
                <p>The server connector registry is currently empty.</p>
              </div>
            </Card>
          )}
          <div className="grid integration-grid">
            {definitions.map((definition) => {
              const Icon = icons[definition.connector_type];
              const connection = byType.get(definition.connector_type);
              const connected =
                connection && connectedStatuses.has(connection.status);
              return (
                <Card
                  className="integration-card"
                  key={definition.connector_type}
                >
                  <div className="integration-card-top">
                    <div className="integration-icon">
                      <Icon />
                    </div>
                    <div className="integration-card-badges">
                      {recommendedSet.has(definition.connector_type) && (
                        <Badge tone="info">Recommended</Badge>
                      )}
                      <Badge tone={connectionTone(connection?.status)}>
                        {connection
                          ? humanize(connection.status)
                          : setupLabel(definition)}
                      </Badge>
                    </div>
                  </div>
                  <h2>{definition.display_name}</h2>
                  <div className="eyebrow">{humanize(definition.category)}</div>
                  <p className="integration-desc">{definition.description}</p>
                  <div className="chip-list integration-capabilities">
                    {definition.read_capabilities.slice(0, 2).map((item) => (
                      <Badge key={item}>{humanize(item)}</Badge>
                    ))}
                  </div>
                  <div className="chip-list integration-capabilities">
                    {providerReadinessBadges(definition, connection).map((item) => (
                      <Badge key={item.id} tone={item.tone}>{item.label}</Badge>
                    ))}
                  </div>
                  {connection ? (
                    <Button
                      variant={connected ? "soft" : "secondary"}
                      className="btn-sm"
                      onClick={() => setSelectedType(definition.connector_type)}
                    >
                      View connection <Link2 />
                    </Button>
                  ) : (
                    <Button
                      variant="secondary"
                      className="btn-sm"
                      disabled={definition.setup_status !== "available"}
                      onClick={() => setConnectTarget(definition)}
                    >
                      {definition.setup_status === "available"
                        ? "Connect"
                        : setupLabel(definition)}{" "}
                      <Link2 />
                    </Button>
                  )}
                </Card>
              );
            })}
          </div>
        </>
      )}
      {connectTarget && (
        <Modal
          title={`Connect ${connectTarget.display_name}`}
          description="Authorization starts on the provider's own screen and returns through a one-time server callback."
          onClose={() => setConnectTarget(null)}
        >
          <ConnectionSteps definition={connectTarget} />
          {error && <p className="form-error">{error}</p>}
          <div className="modal-foot">
            <Button onClick={() => setConnectTarget(null)}>Cancel</Button>
            <Button
              variant="green"
              disabled={
                connectTarget.setup_status !== "available" ||
                authorize.isPending
              }
              onClick={() => authorize.mutate({ definition: connectTarget })}
            >
              {authorize.isPending
                ? "Opening provider…"
                : connectTarget.setup_status === "available"
                  ? "Continue to provider"
                  : setupLabel(connectTarget)}
            </Button>
          </div>
        </Modal>
      )}
      {selectedDefinition && selectedConnection && (
        <Modal
          wide
          title={selectedDefinition.display_name}
          description={
            selectedConnection.external_account_display_name ||
            selectedConnection.external_account_reference ||
            "No provider account metadata recorded"
          }
          onClose={() => {
            setSelectedType(null);
            setError("");
          }}
        >
          <div className="integration-detail-status">
            <div className="integration-icon">
              <Database />
            </div>
            <div className="row-main">
              <div className="eyebrow">Connection status</div>
              <h2>{humanize(selectedConnection.status)}</h2>
              <p className="subtle">
                Connected {formatDate(selectedConnection.connected_at)} · Health
                checked {formatDate(selectedConnection.last_health_check_at)} ·
                Last successful data sync{" "}
                {formatDate(selectedConnection.last_successful_sync_at)}
              </p>
            </div>
            <Badge tone={connectionTone(selectedConnection.status)}>
              {humanize(selectedConnection.health)}
            </Badge>
          </div>
          {(selectedConnection.connector_type === "google_ads" ||
            selectedConnection.connector_type === "meta_ads") && (
            <CommerceCapabilityReadiness connection={selectedConnection} />
          )}
          {error && <p className="form-error">{error}</p>}
          <div className="grid analysis-grid">
            <Card>
              <SectionTitle title="Granted provider access" />
              {selectedConnection.scopes_granted.length ? (
                <>
                  {selectedDefinition.read_capabilities.map((item) => (
                    <div className="check-line" key={item}>
                      <Check /> {humanize(item)}
                    </div>
                  ))}
                  <div className="chip-list integration-scope-list">
                    {selectedConnection.scopes_granted.map((scope) => (
                      <Badge key={scope}>{scopeLabel(scope)}</Badge>
                    ))}
                  </div>
                </>
              ) : (
                <p className="subtle">
                  No provider permissions have been recorded.
                </p>
              )}
              <p className="integration-safety-copy">
                {selectedDefinition.external_writes_enabled
                  ? "Write permissions never create a shortcut: every external action uses policy, approval, spend controls, and a durable attempt."
                  : "Provider writes are not enabled by the platform configuration."}
              </p>
            </Card>
            <Card>
              <SectionTitle title="Selected resources" />
              {selectedConnection.selected_resources.length ? (
                selectedConnection.selected_resources.map((item) => (
                  <div
                    className="list-row"
                    key={`${item.resource_type}:${item.external_reference}`}
                  >
                    <Database />
                    <div className="row-main">
                      <strong>{item.display_name}</strong>
                      <div className="row-copy">
                        {humanize(item.resource_type)}
                      </div>
                    </div>
                  </div>
                ))
              ) : (
                <p className="subtle">No provider resources selected.</p>
              )}
            </Card>
          </div>
          {canReadProvider && (
            <Card style={{ marginTop: 14 }}>
              <SectionTitle
                title="Resource access"
                action={
                  <Badge tone="success">Validated by provider list</Badge>
                }
              />
              {resources.isLoading ? (
                <p className="subtle">Loading available resources…</p>
              ) : resources.error ? (
                <div className="ai-banner">
                  <AlertCircle /> Provider resources are unavailable. The saved
                  selection has not changed.
                </div>
              ) : resources.data?.length ? (
                <div className="integration-resource-picker">
                  <select
                    value={resourceKey}
                    onChange={(event) => setResourceKey(event.target.value)}
                  >
                    {resources.data.map((item) => (
                      <option
                        value={resourceValue(item)}
                        key={resourceValue(item)}
                      >
                        {item.display_name} · {humanize(item.resource_type)}
                      </option>
                    ))}
                  </select>
                  <Button
                    className="btn-sm"
                    disabled={selectResource.isPending || !resourceKey}
                    onClick={() => {
                      const resource = resources.data?.find(
                        (item) => resourceValue(item) === resourceKey,
                      );
                      if (resource) selectResource.mutate(resource);
                    }}
                  >
                    Save selection
                  </Button>
                </div>
              ) : (
                <p className="subtle">
                  The provider returned no selectable resources.
                </p>
              )}
            </Card>
          )}
          <Card style={{ marginTop: 14 }}>
            <SectionTitle
              title="Recent inbound events"
              action={<Badge>{events.data?.total ?? 0} recorded</Badge>}
            />
            {events.isLoading ? (
              <p className="subtle">Loading event history…</p>
            ) : events.error ? (
              <p className="subtle">Event history could not be loaded.</p>
            ) : events.data?.items.length ? (
              events.data.items.slice(0, 5).map((event) => (
                <div className="list-row" key={event.id}>
                  <Clock3 />
                  <div className="row-main">
                    <strong>{humanize(event.event_type)}</strong>
                    <div className="row-copy">
                      {formatDate(event.received_at)}
                    </div>
                  </div>
                  <Badge
                    tone={
                      event.status === "processed"
                        ? "success"
                        : event.status === "failed"
                          ? "danger"
                          : "neutral"
                    }
                  >
                    {event.status}
                  </Badge>
                </div>
              ))
            ) : (
              <p className="subtle">No verified inbound events recorded.</p>
            )}
          </Card>
          <div className="toolbar integration-detail-actions">
            <Button
              variant="soft"
              disabled={!canReadProvider || health.isPending}
              onClick={() => health.mutate(selectedConnection)}
            >
              <RefreshCw className={health.isPending ? "spin" : ""} /> Check
              health
            </Button>
            <Button
              disabled={
                selectedDefinition.setup_status !== "available" ||
                authorize.isPending
              }
              onClick={() =>
                authorize.mutate({
                  definition: selectedDefinition,
                  connection: selectedConnection,
                })
              }
            >
              Reconnect
            </Button>
            <Button
              variant="danger"
              disabled={
                disconnect.isPending || selectedConnection.status === "disabled"
              }
              onClick={() => setDisconnectTarget(selectedConnection)}
            >
              <Unplug /> Disconnect
            </Button>
          </div>
        </Modal>
      )}
      {disconnectTarget && (
        <Modal
          title={`Disconnect ${disconnectTarget.display_name}?`}
          description="This disables the local connection, clears selected resources, revokes the stored credential reference, and blocks future connector use."
          onClose={() => setDisconnectTarget(null)}
        >
          <div className="ai-banner">
            <AlertCircle /> Previously imported business records remain in {PRODUCT_NAME}.
          </div>
          {error && <p className="form-error">{error}</p>}
          <div className="modal-foot">
            <Button onClick={() => setDisconnectTarget(null)}>
              Keep connected
            </Button>
            <Button
              variant="danger"
              disabled={disconnect.isPending}
              onClick={() => disconnect.mutate(disconnectTarget)}
            >
              {disconnect.isPending ? "Disconnecting…" : "Disconnect"}
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}

function ConnectionSteps({ definition }: { definition: ConnectorDefinition }) {
  return (
    <>
      <div className="connection-steps">
        <div className="connection-step">
          <span>1</span>
          <div>
            <strong>Authorize with {definition.display_name}</strong>
            <p>
              The provider handles sign-in; the frontend never asks for provider
              credentials.
            </p>
          </div>
        </div>
        <div className="connection-step">
          <span>2</span>
          <div>
            <strong>Review read access</strong>
            <p>{definition.read_capabilities.map(humanize).join(", ")}.</p>
          </div>
        </div>
        <div className="connection-step">
          <span>3</span>
          <div>
            <strong>Choose business resources</strong>
            <p>
              Only resources returned by the connected provider account can be
              selected.
            </p>
          </div>
        </div>
      </div>
      <div className="prototype-note">
        {definition.external_writes_enabled
          ? "This provider supports governed writes. OAuth authorization alone never bypasses policy, approval, spend limits, or durable dispatch."
          : "Provider write execution is not enabled by the platform configuration."}
      </div>
    </>
  );
}

function resourceValue(
  resource: Pick<
    ExternalIntegrationResource,
    "resource_type" | "external_reference"
  >,
) {
  return `${resource.resource_type}:${resource.external_reference}`;
}

function scopeLabel(scope: string) {
  const segment = scope.split("/").filter(Boolean).at(-1) ?? scope;
  return humanize(segment.replaceAll(".", " "));
}

function CommerceCapabilityReadiness({
  connection,
}: {
  connection: IntegrationConnection;
}) {
  const selected = new Set(
    connection.selected_resources.map((item) => item.resource_type),
  );
  const authorized =
    connectedStatuses.has(connection.status) &&
    connection.authentication_state === "authorized";
  const capabilities =
    connection.connector_type === "google_ads"
      ? ([
          ["Google OAuth", authorized],
          ["Merchant Center account", selected.has("google_merchant_account")],
          ["Merchant data source", selected.has("google_merchant_data_source")],
          ["Google Ads customer", selected.has("google_ads_customer")],
          [
            "Merchant ↔ Ads relationship",
            selected.has("google_merchant_ads_link"),
          ],
          ["Conversion measurement", selected.has("google_conversion_action")],
        ] as const)
      : ([
          ["Meta OAuth", authorized],
          ["Meta Business", selected.has("meta_business")],
          ["Product catalog", selected.has("meta_catalog")],
          ["Ad account", selected.has("ad_account")],
          ["Conversion dataset / pixel", selected.has("conversion_dataset")],
          ["Advertising identity", selected.has("facebook_page")],
        ] as const);
  const ready = capabilities.every(([, value]) => value);
  return (
    <Card style={{ marginTop: 14 }}>
      <SectionTitle
        title="Commerce advertising readiness"
        action={
          <Badge tone={ready ? "success" : "warning"}>
            {ready ? "Ready for preflight" : "Partially configured"}
          </Badge>
        }
      />
      <div className="grid analysis-grid">
        {capabilities.map(([label, value]) => (
          <div className="check-line" key={label}>
            {value ? <Check /> : <AlertCircle />}
            <span>{label}</span>
            <Badge tone={value ? "success" : "warning"}>
              {value ? "ready" : "required"}
            </Badge>
          </div>
        ))}
      </div>
      <p className="integration-safety-copy">
        Readiness only unlocks deterministic preflight. Spend still requires
        policy, approval, AIAction, and a durable execution attempt.
      </p>
    </Card>
  );
}
