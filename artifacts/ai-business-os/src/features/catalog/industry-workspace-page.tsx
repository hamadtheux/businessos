import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link } from "wouter";
import {
  AlertCircle,
  Archive,
  Check,
  ChevronDown,
  ClipboardPaste,
  FileSpreadsheet,
  MoreHorizontal,
  Package,
  Pencil,
  Plus,
  Rocket,
  Store,
  RefreshCw,
  RotateCcw,
  Search,
} from "lucide-react";
import { useBusiness } from "@/business-context";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Modal,
  PageHeader,
  SectionTitle,
} from "@/components/product-ui";
import { catalogApi, humanizeCatalogError } from "@/services/catalog";
import {
  commerceApi,
  type CommerceConnection,
  type CommerceImportPreview,
  type CommerceProviderDefinition,
  type CommerceSyncIssue,
  type CommerceSyncRun,
  type FeedDestination,
  type FeedProductStatus,
} from "@/services/commerce";
import type {
  CatalogItem,
  CatalogItemStatus,
  CatalogItemType,
} from "@/services/api-types";
import { CatalogImportDialog } from "./catalog-import-dialog";
import { CatalogItemDialog } from "./catalog-item-dialog";
import { formatCatalogPrice, isCurrentCatalogResponse } from "./catalog-model";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  STORE_PROVIDER_KEYS,
  catalogCountLabel,
  catalogSourceLabel,
  catalogStatusLabel,
  commerceConnectionStatus,
  commerceProviderLabel,
  feedDestinationStatus,
  formatLastSync,
  providerAvailability,
  syncRunMode,
  syncRunStatus,
} from "./catalog-presentation";

type LoadState = "loading" | "success" | "error";
type StatusFilter = "default" | CatalogItemStatus;


function CatalogHeaderActions({
  onAdd,
  onConnect,
  onPaste,
  onUpload,
}: {
  onAdd: () => void;
  onConnect: () => void;
  onPaste: () => void;
  onUpload: () => void;
}) {
  return (
    <div className="toolbar">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="soft">
            <FileSpreadsheet />
            Import
            <ChevronDown />
          </Button>
        </DropdownMenuTrigger>

        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={onUpload}>
            <FileSpreadsheet />
            Upload CSV / Excel
          </DropdownMenuItem>

          <DropdownMenuItem onSelect={onPaste}>
            <ClipboardPaste />
            Paste a product list
          </DropdownMenuItem>

          <DropdownMenuSeparator />

          <DropdownMenuItem onSelect={onConnect}>
            <Store />
            Connect a store
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Button variant="green" onClick={onAdd}>
        <Plus />
        Add product
      </Button>
    </div>
  );
}

export function IndustryWorkspacePage() {
  const { activeBusiness, activeBusinessId } = useBusiness();
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [loadError, setLoadError] = useState("");
  const [itemType, setItemType] = useState<"all" | CatalogItemType>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("default");
  const [query, setQuery] = useState("");
  const [editor, setEditor] = useState<"create" | CatalogItem | null>(null);
  const [importMode, setImportMode] = useState<"upload" | "paste" | null>(null);
  const [archiveItem, setArchiveItem] = useState<CatalogItem | null>(null);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [reloadVersion, setReloadVersion] = useState(0);
  const [connections, setConnections] = useState<CommerceConnection[]>([]);
  const [providerDefinitions, setProviderDefinitions] = useState<
    CommerceProviderDefinition[]
  >([]);
  const [syncRuns, setSyncRuns] = useState<Record<string, CommerceSyncRun[]>>(
    {},
  );
  const [syncIssues, setSyncIssues] = useState<
    Record<string, CommerceSyncIssue[]>
  >({});
  const [feedDestinations, setFeedDestinations] = useState<FeedDestination[]>(
    [],
  );
  const [feedProductStatuses, setFeedProductStatuses] = useState<
    Record<string, FeedProductStatus[]>
  >({});
  const [viewingFeedId, setViewingFeedId] = useState("");
  const [connectingStore, setConnectingStore] = useState(false);
  const [configuringStore, setConfiguringStore] =
    useState<CommerceConnection | null>(null);
  const [importingSource, setImportingSource] =
    useState<CommerceConnection | null>(null);
  const [commerceImportPreview, setCommerceImportPreview] =
    useState<CommerceImportPreview | null>(null);
  const [commerceBusy, setCommerceBusy] = useState(false);
  const requestVersion = useRef(0);
  const activeBusinessIdRef = useRef(activeBusinessId);
  activeBusinessIdRef.current = activeBusinessId;

  useEffect(() => {
    setEditor(null);
    setImportMode(null);
    setArchiveItem(null);
    setNotice("");
    setActionError("");
  }, [activeBusinessId]);

  useEffect(() => {
    const controller = new AbortController();
    if (!activeBusinessId) return () => controller.abort();
    void Promise.all([
      commerceApi.providers(activeBusinessId, controller.signal),
      commerceApi.connections.list(activeBusinessId, controller.signal),
      commerceApi.feeds.list(activeBusinessId, controller.signal),
    ])
      .then(async ([providers, connectionItems, destinations]) => {
        if (controller.signal.aborted) return;
        const histories = await Promise.all(
          connectionItems.map(
            async (connection) =>
              [
                connection.id,
                await commerceApi.connections
                  .syncRuns(activeBusinessId, connection.id, controller.signal)
                  .catch(() => []),
              ] as const,
          ),
        );
        const issueEntries = await Promise.all(
          histories.flatMap(([, runs]) => {
            const latest = runs[0];
            return latest
              ? [
                  commerceApi
                    .syncIssues(activeBusinessId, latest.id, controller.signal)
                    .then((issues) => [latest.id, issues] as const)
                    .catch(() => [latest.id, []] as const),
                ]
              : [];
          }),
        );
        if (controller.signal.aborted) return;
        setProviderDefinitions(providers);
        setConnections(connectionItems);
        setFeedDestinations(destinations);
        setSyncRuns(Object.fromEntries(histories));
        setSyncIssues(Object.fromEntries(issueEntries));
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setActionError(
            humanizeCatalogError(
              reason,
              "Commerce connection status could not be loaded.",
            ),
          );
        }
      });
    return () => controller.abort();
  }, [activeBusinessId, reloadVersion]);

  useEffect(() => {
    const businessId = activeBusinessId;
    const version = ++requestVersion.current;
    const controller = new AbortController();
    setItems([]);
    setLoadState("loading");
    setLoadError("");

    if (!businessId) {
      setLoadState("success");
      return () => controller.abort();
    }

    void catalogApi
      .listCatalogItems(
        businessId,
        {
          itemType: itemType === "all" ? undefined : itemType,
          status: statusFilter === "default" ? undefined : statusFilter,
        },
        controller.signal,
      )
      .then((catalogItems) => {
        if (
          isCurrentCatalogResponse(
            businessId,
            version,
            activeBusinessIdRef.current,
            requestVersion.current,
          )
        ) {
          setItems(catalogItems);
          setLoadState("success");
        }
      })
      .catch((reason: unknown) => {
        if (
          controller.signal.aborted ||
          !isCurrentCatalogResponse(
            businessId,
            version,
            activeBusinessIdRef.current,
            requestVersion.current,
          )
        ) {
          return;
        }
        setLoadError(
          humanizeCatalogError(
            reason,
            "We couldn't load this catalog. Please try again.",
          ),
        );
        setLoadState("error");
      });

    return () => controller.abort();
  }, [activeBusinessId, itemType, reloadVersion, statusFilter]);

  const visibleItems = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) return items;
    return items.filter((item) =>
      [item.name, item.sku ?? "", item.description ?? ""].some((value) =>
        value.toLowerCase().includes(normalizedQuery),
      ),
    );
  }, [items, query]);

  const reload = () => setReloadVersion((current) => current + 1);

  const handleSaved = (savedItem: CatalogItem) => {
    if (savedItem.business_id !== activeBusinessIdRef.current) return;
    setItems((current) => {
      const exists = current.some((item) => item.id === savedItem.id);
      return exists
        ? current.map((item) => (item.id === savedItem.id ? savedItem : item))
        : [...current, savedItem];
    });
    setEditor(null);
    setNotice(
      `${savedItem.item_type === "product" ? "Product" : "Service"} saved`,
    );
    reload();
  };

  const archiveSelected = async () => {
    if (!archiveItem) return;
    const businessId = activeBusinessIdRef.current;
    setArchiveBusy(true);
    setActionError("");
    try {
      await catalogApi.archiveCatalogItem(businessId, archiveItem.id);
      if (businessId === activeBusinessIdRef.current) {
        setItems((current) =>
          current.filter((item) => item.id !== archiveItem.id),
        );
        setArchiveItem(null);
        setNotice("Item archived. Historical data is preserved.");
        reload();
      }
    } catch (reason) {
      setActionError(
        humanizeCatalogError(
          reason,
          "We couldn't archive this item. Please try again.",
        ),
      );
    } finally {
      setArchiveBusy(false);
    }
  };

  const restoreItem = async (item: CatalogItem) => {
    const businessId = activeBusinessIdRef.current;
    setActionError("");
    try {
      const restored = await catalogApi.updateCatalogItem(businessId, item.id, {
        status: "active",
      });
      if (restored.business_id === activeBusinessIdRef.current) {
        setNotice("Item restored to the active catalog.");
        reload();
      }
    } catch (reason) {
      setActionError(
        humanizeCatalogError(
          reason,
          "We couldn't restore this item. Please try again.",
        ),
      );
    }
  };

  const currency = activeBusiness?.currency ?? "USD";
  const locale = activeBusiness?.locale ?? "en";

  const createCommerceConnection = async (
    event: FormEvent<HTMLFormElement>,
  ) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setCommerceBusy(true);
    setActionError("");
    try {
      const provider = String(
        form.get("provider"),
      ) as CommerceConnection["provider"];
      const record = await commerceApi.connections.create(activeBusinessId, {
        provider,
        display_name: String(form.get("display_name")),
        store_url: String(form.get("store_url")) || null,
        external_account_id: String(form.get("external_account_id")) || null,
      });
      setConnectingStore(false);
      if (
        [
          "shopify",
          "woocommerce",
          "bigcommerce",
          "magento",
          "custom_api",
        ].includes(record.provider)
      ) {
        setConfiguringStore(record);
      } else if (
        ["csv", "xml_feed", "google_product_feed"].includes(record.provider)
      ) {
        setCommerceImportPreview(null);
        setImportingSource(record);
      }
      setNotice(
        ["csv", "xml_feed", "google_product_feed"].includes(record.provider)
          ? `${record.display_name} is ready for a local, validated import.`
          : `${record.display_name} was added. Provider configuration is required before any data can synchronize.`,
      );
      reload();
    } catch (reason) {
      setActionError(
        humanizeCatalogError(reason, "The commerce source could not be added."),
      );
    } finally {
      setCommerceBusy(false);
    }
  };

  const configureCommerceConnection = async (
    event: FormEvent<HTMLFormElement>,
  ) => {
    event.preventDefault();
    if (!configuringStore) return;
    const form = new FormData(event.currentTarget);
    const credentials = Object.fromEntries(
      [...form.entries()]
        .filter(([key, value]) => key !== "provider" && String(value).trim())
        .map(([key, value]) => [key, String(value).trim()]),
    );
    setCommerceBusy(true);
    setActionError("");
    try {
      const connected = await commerceApi.connections.configure(
        activeBusinessIdRef.current,
        configuringStore.id,
        credentials,
      );
      setConfiguringStore(null);
      setNotice(
        `${connected.store_name ?? connected.display_name} authenticated. Start the initial sync when ready.`,
      );
      reload();
    } catch (reason) {
      setActionError(
        humanizeCatalogError(
          reason,
          "The store could not be authenticated. Check the provider credentials and URL.",
        ),
      );
    } finally {
      setCommerceBusy(false);
    }
  };

  const syncCommerceConnection = async (connection: CommerceConnection) => {
    setCommerceBusy(true);
    setActionError("");
    try {
      const run = await commerceApi.connections.sync(
        activeBusinessIdRef.current,
        connection.id,
        connection.last_success_at ? "incremental" : "initial",
      );
      setNotice(
        `${connection.store_name ?? connection.display_name} sync queued (${run.mode}). Progress is durable and can resume safely.`,
      );
      reload();
    } catch (reason) {
      setActionError(
        humanizeCatalogError(reason, "The store sync could not be queued."),
      );
    } finally {
      setCommerceBusy(false);
    }
  };

  const selectedImportFile = (form: HTMLFormElement) => {
    const value = new FormData(form).get("upload");
    if (!(value instanceof File) || value.size === 0) {
      throw new Error("Choose a non-empty product file first.");
    }
    return value;
  };

  const previewCommerceImport = async (form: HTMLFormElement) => {
    if (!importingSource) return;
    setCommerceBusy(true);
    setActionError("");
    try {
      const file = selectedImportFile(form);
      const preview = await commerceApi.imports.preview(
        activeBusinessIdRef.current,
        importingSource.provider as CommerceImportPreview["file_type"],
        file,
      );
      setCommerceImportPreview(preview);
    } catch (reason) {
      setActionError(
        humanizeCatalogError(
          reason,
          "The product file could not be previewed.",
        ),
      );
    } finally {
      setCommerceBusy(false);
    }
  };

  const applyCommerceImport = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!importingSource) return;
    setCommerceBusy(true);
    setActionError("");
    try {
      const file = selectedImportFile(event.currentTarget);
      const result = await commerceApi.imports.apply(
        activeBusinessIdRef.current,
        importingSource.id,
        importingSource.provider as CommerceImportPreview["file_type"],
        file,
        `ui-import:${importingSource.id}:${file.size}:${file.lastModified}`,
      );
      setImportingSource(null);
      setCommerceImportPreview(null);
      setNotice(
        `${result.products_created} products created, ${result.products_updated} updated, and ${result.products_failed} rejected safely.`,
      );
      reload();
    } catch (reason) {
      setActionError(
        humanizeCatalogError(reason, "The product file could not be imported."),
      );
    } finally {
      setCommerceBusy(false);
    }
  };

  const synchronizeFeed = async (
    destinationId: string,
    reconcileOnly = false,
  ) => {
    setCommerceBusy(true);
    setActionError("");
    try {
      const result = await commerceApi.feeds.sync(
        activeBusinessIdRef.current,
        destinationId,
        reconcileOnly,
      );
      setNotice(
        reconcileOnly
          ? `Provider state reconciled: ${result.eligible_count} eligible, ${result.limited_count} limited, ${result.rejected_count} ineligible.`
          : `${result.submitted_count} products submitted; provider processing and eligibility remain separate states.`,
      );
      reload();
    } catch (reason) {
      setActionError(
        humanizeCatalogError(
          reason,
          "The provider destination could not be synchronized.",
        ),
      );
    } finally {
      setCommerceBusy(false);
    }
  };

  const viewFeedIssues = async (destinationId: string) => {
    if (viewingFeedId === destinationId) {
      setViewingFeedId("");
      return;
    }
    setCommerceBusy(true);
    setActionError("");
    try {
      const statuses = await commerceApi.feeds.products(
        activeBusinessIdRef.current,
        destinationId,
      );
      setFeedProductStatuses((current) => ({
        ...current,
        [destinationId]: statuses,
      }));
      setViewingFeedId(destinationId);
    } catch (reason) {
      setActionError(
        humanizeCatalogError(
          reason,
          "Provider product issues could not be loaded.",
        ),
      );
    } finally {
      setCommerceBusy(false);
    }
  };

  const supportedStoreProviders = providerDefinitions.filter(
    (provider) =>
      STORE_PROVIDER_KEYS.includes(provider.provider) &&
      providerAvailability(provider.implementation_status).selectable,
  );

  return (
    <>
      <PageHeader
        eyebrow="Business catalog"
        title="Products & services"
        subtitle="Manage everything your business sells from one place."
        action={
          <CatalogHeaderActions
            onAdd={() => setEditor("create")}
            onConnect={() => setConnectingStore(true)}
            onPaste={() => setImportMode("paste")}
            onUpload={() => setImportMode("upload")}
          />
        }
      />

      {notice && (
        <div className="catalog-notice" role="status">
          <Check /> {notice}
        </div>
      )}
      {actionError && (
        <div className="catalog-inline-error" role="alert">
          <AlertCircle /> {actionError}
        </div>
      )}

      <Card className="catalog-store-card">
        <div className="catalog-store-banner">
          <div className="catalog-store-icon" aria-hidden="true">
            <Store />
          </div>
          <div className="catalog-store-copy">
            <div className="eyebrow">
              {connections.length ? "Store connections" : "Automatic catalog sync"}
            </div>
            <h2>
              {connections.length ? "Your connected stores" : "Connect your store"}
            </h2>
            <p>
              Automatically keep your catalog in sync with the platform you
              already use.
            </p>
            {supportedStoreProviders.length > 0 && (
              <div className="catalog-supported-providers">
                <span>Supported</span>
                <span aria-label="Supported store platforms">
                  {supportedStoreProviders
                    .map((provider) => provider.display_name)
                    .join(" • ")}
                </span>
              </div>
            )}
          </div>
          <div className="catalog-store-actions">
            <Button
              variant={connections.length ? "secondary" : "green"}
              onClick={() => setConnectingStore(true)}
            >
              <Store /> Connect store
            </Button>
            <Link href="/integrations">Manage integrations →</Link>
          </div>
        </div>

        {connections.length > 0 && (
          <div className="catalog-connection-list">
            {connections.map((connection) => {
              const status = commerceConnectionStatus(connection);
              const recentRun = syncRuns[connection.id]?.[0];
              const productCount = recentRun
                ? recentRun.products_created + recentRun.products_updated
                : 0;
              const isStoreProvider = [
                "shopify",
                "woocommerce",
                "bigcommerce",
                "magento",
                "custom_api",
              ].includes(connection.provider);
              const isFileProvider = [
                "csv",
                "xml_feed",
                "google_product_feed",
              ].includes(connection.provider);
              return (
                <div className="catalog-connection" key={connection.id}>
                  <div className="catalog-connection-summary">
                    <div className="catalog-provider-mark" aria-hidden="true">
                      <Store />
                    </div>
                    <div className="catalog-connection-copy">
                      <div className="catalog-connection-title">
                        <strong>
                          {connection.store_name ?? connection.display_name}
                        </strong>
                        <Badge tone={status.tone}>{status.label}</Badge>
                      </div>
                      <div className="catalog-connection-meta">
                        <span>{commerceProviderLabel(connection.provider)}</span>
                        <span>{formatLastSync(connection.last_success_at)}</span>
                        {recentRun && (
                          <span>
                            {productCount.toLocaleString()}{" "}
                            {productCount === 1 ? "product" : "products"} updated
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="catalog-connection-actions">
                      {isStoreProvider &&
                        [
                          "configuration_required",
                          "connection_required",
                          "authentication_expired",
                        ].includes(connection.status) && (
                          <Button
                            disabled={commerceBusy}
                            onClick={() => setConfiguringStore(connection)}
                          >
                            Finish setup
                          </Button>
                        )}
                      {isFileProvider && (
                        <Button
                          disabled={commerceBusy}
                          onClick={() => {
                            setCommerceImportPreview(null);
                            setImportingSource(connection);
                          }}
                        >
                          <FileSpreadsheet /> Import
                        </Button>
                      )}
                      {isStoreProvider &&
                        [
                          "connected",
                          "attention_required",
                          "rate_limited",
                          "failed",
                        ].includes(connection.status) && (
                          <Button
                            disabled={
                              commerceBusy ||
                              connection.status === "rate_limited"
                            }
                            onClick={() =>
                              void syncCommerceConnection(connection)
                            }
                          >
                            <RefreshCw /> Sync now
                          </Button>
                        )}
                    </div>
                  </div>
                  {(syncRuns[connection.id]?.length ?? 0) > 0 && (
                    <details className="catalog-sync-history">
                      <summary>Recent sync activity</summary>
                      <div className="catalog-sync-list">
                        {syncRuns[connection.id].slice(0, 5).map((run) => (
                          <div className="catalog-sync-row" key={run.id}>
                            <div>
                              <strong>{syncRunStatus(run.status)}</strong>
                              <span>
                                {syncRunMode(run.mode)} ·{" "}
                                {new Date(run.created_at).toLocaleString()} ·{" "}
                                {formatSyncDuration(run)}
                              </span>
                            </div>
                            <span>
                              {(
                                run.products_created + run.products_updated
                              ).toLocaleString()}{" "}
                              products
                            </span>
                            {(run.warnings > 0 || run.failures > 0) && (
                              <span className="catalog-sync-warning">
                                {run.warnings + run.failures}{" "}
                                {run.warnings + run.failures === 1
                                  ? "item"
                                  : "items"}{" "}
                                to review
                              </span>
                            )}
                            {(syncIssues[run.id] ?? [])
                              .slice(0, 3)
                              .map((issue) => (
                                <p key={issue.id}>{issue.message}</p>
                              ))}
                          </div>
                        ))}
                      </div>
                    </details>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {feedDestinations.length > 0 && (
          <details className="catalog-feed-panel">
            <summary>
              Shopping feeds
              <span>
                {feedDestinations.length}{" "}
                {feedDestinations.length === 1 ? "channel" : "channels"}
              </span>
            </summary>
            <div className="catalog-feed-list">
              {feedDestinations.map((destination) => {
                const destinationStatus = feedDestinationStatus(
                  destination.status,
                );
                const issues =
                  feedProductStatuses[destination.id]?.filter(
                    (item) =>
                      item.provider_issues.length ||
                      item.missing_attributes.length ||
                      item.warnings.length,
                  ) ?? [];
                const accountIssues = Array.isArray(
                  destination.safe_metadata.account_issues,
                )
                  ? destination.safe_metadata.account_issues
                  : [];
                return (
                  <div className="catalog-feed-row" key={destination.id}>
                    <div className="catalog-feed-summary">
                      <Rocket aria-hidden="true" />
                      <div>
                        <div className="catalog-connection-title">
                          <strong>{destination.display_name}</strong>
                          <Badge tone={destinationStatus.tone}>
                            {destinationStatus.label}
                          </Badge>
                        </div>
                        <div className="catalog-connection-meta">
                          <span>
                            {destination.provider === "google_merchant_center"
                              ? "Google Merchant Center"
                              : "Meta product catalog"}
                          </span>
                          <span>
                            {destination.eligible_count.toLocaleString()} ready
                          </span>
                          <span>
                            {destination.limited_count +
                              destination.rejected_count}{" "}
                            to review
                          </span>
                          <span>
                            {formatLastSync(
                              destination.last_synchronized_at,
                            )}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div className="catalog-feed-actions">
                      <Button
                        disabled={commerceBusy}
                        onClick={() =>
                          void synchronizeFeed(destination.id)
                        }
                      >
                        <RefreshCw /> Sync now
                      </Button>
                      <Button
                        disabled={commerceBusy}
                        onClick={() =>
                          void synchronizeFeed(destination.id, true)
                        }
                      >
                        Check status
                      </Button>
                      <Button
                        variant="tertiary"
                        disabled={commerceBusy}
                        onClick={() => void viewFeedIssues(destination.id)}
                      >
                        {viewingFeedId === destination.id
                          ? "Hide issues"
                          : "View issues"}
                      </Button>
                    </div>
                    {viewingFeedId === destination.id && (
                      <div className="catalog-feed-issues" role="status">
                        <strong>Items to review</strong>
                        {!issues.length && !accountIssues.length && (
                          <p>
                            Nothing needs your attention right now. Some items
                            may still be processing.
                          </p>
                        )}
                        {accountIssues.slice(0, 10).map((issue, index) => (
                          <p key={destination.id + ":account:" + index}>
                            {String(
                              (issue as Record<string, unknown>).message ??
                                "This account needs attention.",
                            )}
                          </p>
                        ))}
                        {issues.slice(0, 20).map((item) => (
                          <div key={item.id}>
                            <p>
                              This item has{" "}
                              {item.missing_attributes.length +
                                item.warnings.length +
                                item.provider_issues.length}{" "}
                              details to review.
                            </p>
                            {item.provider_issues
                              .slice(0, 5)
                              .map((issue, index) => (
                                <p
                                  key={
                                    destination.id +
                                    ":product:" +
                                    item.id +
                                    ":" +
                                    index
                                  }
                                >
                                  {String(
                                    issue.message ??
                                      "This item needs provider review.",
                                  )}
                                </p>
                              ))}
                          </div>
                        ))}
                        <Link href="/integrations" className="btn btn-sm">
                          Manage channel
                        </Link>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </details>
        )}
      </Card>

      <Card className="table-card catalog-workspace-card" pad={false}>
        <div className="table-toolbar catalog-toolbar">
          <div>
            <div className="eyebrow">Live catalog</div>
            <h2>
              {loadState === "loading"
                ? "Loading items…"
                : statusFilter === "archived"
                  ? `${items.length.toLocaleString()} archived ${items.length === 1 ? "item" : "items"}`
                  : catalogCountLabel(items)}
            </h2>
          </div>
          <div className="catalog-filter-bar">
            <div className="search-box catalog-search">
              <Search />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search products or services"
                aria-label="Search products or services"
              />
            </div>
            <select
              value={itemType}
              onChange={(event) =>
                setItemType(event.target.value as "all" | CatalogItemType)
              }
              aria-label="Filter catalog type"
            >
              <option value="all">All types</option>
              <option value="product">Products</option>
              <option value="service">Services</option>
            </select>
            <select
              value={statusFilter}
              onChange={(event) =>
                setStatusFilter(event.target.value as StatusFilter)
              }
              aria-label="Filter catalog status"
            >
              <option value="default">Current</option>
              <option value="active">Active</option>
              <option value="draft">Draft</option>
              <option value="archived">Archived</option>
            </select>
          </div>
        </div>

        {loadState === "loading" && <CatalogLoadingState />}

        {loadState === "error" && (
          <div className="empty catalog-state-panel">
            <AlertCircle />
            <h3>We couldn't load this catalog</h3>
            <p>{loadError}</p>
            <Button variant="green" onClick={reload}>
              <RefreshCw /> Try again
            </Button>
          </div>
        )}

        {loadState === "success" && items.length === 0 && (
          <CatalogEmptyState
            archived={statusFilter === "archived"}
            onUpload={() => setImportMode("upload")}
            onPaste={() => setImportMode("paste")}
            onManual={() => setEditor("create")}
          />
        )}

        {loadState === "success" && items.length > 0 && (
          <div className="table-scroll catalog-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>SKU</th>
                  <th>Price</th>
                  <th>Status</th>
                  <th>Source</th>
                  <th aria-label="Actions" />
                </tr>
              </thead>
              <tbody>
                {visibleItems.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <strong>{item.name}</strong>
                      {item.description && (
                        <span className="catalog-row-description">
                          {item.description}
                        </span>
                      )}
                    </td>
                    <td className="catalog-type-cell">
                      {item.item_type === "product" ? "Product" : "Service"}
                    </td>
                    <td>{item.sku ?? "—"}</td>
                    <td>{formatCatalogPrice(item.price, currency, locale)}</td>
                    <td>
                      <Badge
                        tone={
                          item.status === "active"
                            ? "success"
                            : item.status === "archived"
                              ? "neutral"
                              : "warning"
                        }
                      >
                        {catalogStatusLabel(item.status)}
                      </Badge>
                    </td>
                    <td>
                      <Badge
                        tone={
                          item.source === "manual"
                            ? "neutral"
                            : item.sync_state === "in_sync"
                              ? "success"
                              : "warning"
                        }
                      >
                        {catalogSourceLabel(item)}
                      </Badge>
                    </td>
                    <td>
                      <div className="catalog-row-actions">
                        {item.item_type === "product" &&
                          item.status !== "archived" && (
                            <Link
                              className="btn btn-sm btn-green catalog-ai-action"
                              href={`/campaigns?new=1&product=${encodeURIComponent(item.id)}`}
                            >
                              <Rocket /> Promote with AI
                            </Link>
                          )}

                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <button
                              className="icon-btn catalog-more-btn"
                              aria-label={`More actions for ${item.name}`}
                            >
                              <MoreHorizontal />
                            </button>
                          </DropdownMenuTrigger>

                          <DropdownMenuContent
                            align="end"
                            sideOffset={7}
                            className="catalog-row-menu"
                          >
                            {item.status === "archived" ? (
                              <DropdownMenuItem
                                className="catalog-row-menu-item"
                                onSelect={() => void restoreItem(item)}
                              >
                                <RotateCcw />
                                <span>Restore item</span>
                              </DropdownMenuItem>
                            ) : (
                              <>
                                <DropdownMenuItem
                                  className="catalog-row-menu-item"
                                  onSelect={() => setEditor(item)}
                                >
                                  <Pencil />
                                  <span>Edit item</span>
                                </DropdownMenuItem>

                                <DropdownMenuSeparator className="catalog-row-menu-separator" />

                                <DropdownMenuItem
                                  className="catalog-row-menu-item catalog-row-menu-danger"
                                  onSelect={() => setArchiveItem(item)}
                                >
                                  <Archive />
                                  <span>Archive item</span>
                                </DropdownMenuItem>
                              </>
                            )}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {visibleItems.length === 0 && (
              <div className="empty compact-empty">
                <Search />
                <h3>No matches in this loaded view</h3>
                <p>Try a different text search or server filter.</p>
              </div>
            )}
          </div>
        )}
      </Card>

      {editor && activeBusiness && (
        <CatalogItemDialog
          key={editor === "create" ? "create" : editor.id}
          businessId={activeBusiness.id}
          businessName={activeBusiness.name}
          currency={currency}
          item={editor === "create" ? undefined : editor}
          onClose={() => setEditor(null)}
          onSaved={handleSaved}
        />
      )}

      {importMode && activeBusiness && (
        <CatalogImportDialog
          key={`${activeBusiness.id}-${importMode}`}
          businessId={activeBusiness.id}
          currency={currency}
          locale={locale}
          mode={importMode}
          onClose={() => setImportMode(null)}
          onImported={(createdCount) => {
            if (activeBusiness.id !== activeBusinessIdRef.current) return;
            setImportMode(null);
            setNotice(`${createdCount} items imported`);
            reload();
          }}
        />
      )}

      {archiveItem && (
        <Modal
          title="Archive catalog item?"
          description="This removes the item from the current catalog while preserving its historical record."
          onClose={() => setArchiveItem(null)}
        >
          <p className="catalog-confirm-copy">
            Archive <strong>{archiveItem.name}</strong>? You can find and
            restore it later using the Archived filter.
          </p>
          {actionError && (
            <div className="catalog-inline-error" role="alert">
              <AlertCircle /> {actionError}
            </div>
          )}
          <div className="modal-foot">
            <Button onClick={() => setArchiveItem(null)} disabled={archiveBusy}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() => void archiveSelected()}
              disabled={archiveBusy}
            >
              {archiveBusy ? <RefreshCw className="spin" /> : <Archive />}
              Archive item
            </Button>
          </div>
        </Modal>
      )}
      {connectingStore && (
        <Modal
          title="Connect an existing store"
          description="Add the source identity now. External providers remain configuration-required until their authenticated adapter and credentials are available."
          onClose={() => setConnectingStore(false)}
        >
          <form onSubmit={createCommerceConnection}>
            <div className="form-grid">
              <div className="field">
                <label>Provider</label>
                <select name="provider" defaultValue="woocommerce">
                  <option value="shopify">Shopify</option>
                  <option value="woocommerce">WooCommerce</option>
                  <option value="bigcommerce">BigCommerce</option>
                  <option value="magento">Magento / Adobe Commerce</option>
                  <option value="custom_api">Custom API</option>
                  <option value="website">Website discovery</option>
                  <option value="xml_feed">XML feed</option>
                  <option value="google_product_feed">Google-style feed</option>
                  <option value="csv">CSV import</option>
                </select>
              </div>
              <div className="field">
                <label>Connection name</label>
                <input
                  name="display_name"
                  required
                  maxLength={160}
                  placeholder="Main online store"
                />
              </div>
              <div className="field full">
                <label>Store URL</label>
                <input
                  name="store_url"
                  type="url"
                  placeholder="https://store.example.com"
                />
              </div>
              <div className="field full">
                <label>External account ID (when known)</label>
                <input name="external_account_id" maxLength={255} />
              </div>
            </div>
            <div className="modal-foot">
              <Button type="button" onClick={() => setConnectingStore(false)}>
                Cancel
              </Button>
              <Button variant="green" type="submit" disabled={commerceBusy}>
                {commerceBusy ? "Adding…" : "Add commerce source"}
              </Button>
            </div>
          </form>
        </Modal>
      )}
      {configuringStore && (
        <Modal
          title={`Authenticate ${configuringStore.display_name}`}
          description="Credentials are sent once to secure credential storage and are never returned to this browser."
          onClose={() => setConfiguringStore(null)}
        >
          <form onSubmit={configureCommerceConnection}>
            <input
              type="hidden"
              name="provider"
              value={configuringStore.provider}
            />
            <CommerceCredentialFields provider={configuringStore.provider} />
            <div className="modal-foot">
              <Button type="button" onClick={() => setConfiguringStore(null)}>
                Cancel
              </Button>
              <Button variant="green" type="submit" disabled={commerceBusy}>
                {commerceBusy ? "Authenticating…" : "Authenticate store"}
              </Button>
            </div>
          </form>
        </Modal>
      )}
      {importingSource && (
        <Modal
          title={`Import ${importingSource.display_name}`}
          description="Preview and validate the provider feed before applying it. Re-importing the same external IDs updates existing products instead of duplicating them."
          onClose={() => {
            setImportingSource(null);
            setCommerceImportPreview(null);
          }}
        >
          <form onSubmit={applyCommerceImport}>
            <div className="field">
              <label>Product file</label>
              <input
                name="upload"
                type="file"
                required
                accept={
                  importingSource.provider === "csv"
                    ? ".csv,text/csv"
                    : ".xml,application/xml,text/xml"
                }
                onChange={() => setCommerceImportPreview(null)}
              />
            </div>
            {commerceImportPreview && (
              <div className="recommendation-strip" role="status">
                <FileSpreadsheet />
                <div>
                  <strong>
                    {commerceImportPreview.products.length} valid preview
                    products
                  </strong>
                  <p>
                    {commerceImportPreview.failures.length} rejected items ·
                    detected fields:{" "}
                    {commerceImportPreview.detected_fields
                      .slice(0, 8)
                      .join(", ") || "none"}
                    {commerceImportPreview.truncated
                      ? " · preview limited to the first 25 items"
                      : ""}
                  </p>
                </div>
              </div>
            )}
            <div className="modal-foot">
              <Button
                type="button"
                onClick={() => {
                  setImportingSource(null);
                  setCommerceImportPreview(null);
                }}
                disabled={commerceBusy}
              >
                Cancel
              </Button>
              <Button
                type="button"
                disabled={commerceBusy}
                onClick={(event) => {
                  const form = event.currentTarget.form;
                  if (form) void previewCommerceImport(form);
                }}
              >
                {commerceBusy ? "Checking…" : "Preview"}
              </Button>
              <Button variant="green" type="submit" disabled={commerceBusy}>
                {commerceBusy ? "Importing…" : "Import products"}
              </Button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}

function CommerceCredentialFields({
  provider,
}: {
  provider: CommerceConnection["provider"];
}) {
  if (provider === "shopify")
    return (
      <div className="form-grid">
        <div className="field full">
          <label>Admin API access token</label>
          <input
            name="access_token"
            type="password"
            required
            autoComplete="off"
          />
        </div>
        <div className="field full">
          <label>Webhook signing secret</label>
          <input name="webhook_secret" type="password" autoComplete="off" />
        </div>
      </div>
    );
  if (provider === "woocommerce")
    return (
      <div className="form-grid">
        <div className="field">
          <label>Consumer key</label>
          <input
            name="consumer_key"
            type="password"
            required
            autoComplete="off"
          />
        </div>
        <div className="field">
          <label>Consumer secret</label>
          <input
            name="consumer_secret"
            type="password"
            required
            autoComplete="off"
          />
        </div>
        <div className="field full">
          <label>Webhook signing secret</label>
          <input name="webhook_secret" type="password" autoComplete="off" />
        </div>
      </div>
    );
  if (provider === "bigcommerce")
    return (
      <div className="form-grid">
        <div className="field">
          <label>Store hash</label>
          <input name="store_hash" required autoComplete="off" />
        </div>
        <div className="field">
          <label>Access token</label>
          <input
            name="access_token"
            type="password"
            required
            autoComplete="off"
          />
        </div>
        <div className="field full">
          <label>Webhook signing secret</label>
          <input name="webhook_secret" type="password" autoComplete="off" />
        </div>
      </div>
    );
  if (provider === "magento")
    return (
      <div className="form-grid">
        <div className="field full">
          <label>Integration access token</label>
          <input
            name="access_token"
            type="password"
            required
            autoComplete="off"
          />
        </div>
        <div className="field full">
          <label>Adobe webhook public verification key (PEM)</label>
          <textarea
            name="webhook_public_key"
            rows={7}
            autoComplete="off"
            placeholder="-----BEGIN PUBLIC KEY-----"
          />
        </div>
        <details className="field full">
          <summary>Legacy/custom Magento webhook</summary>
          <div className="field full">
            <label>HMAC signing secret</label>
            <input name="webhook_secret" type="password" autoComplete="off" />
          </div>
        </details>
      </div>
    );
  return (
    <div className="form-grid">
      <div className="field full">
        <label>API token</label>
        <input name="api_token" type="password" required autoComplete="off" />
      </div>
      <details className="field full" open>
        <summary>Advanced endpoint configuration</summary>
        <div className="field full">
          <label>Constrained endpoint configuration</label>
          <textarea
            name="configuration"
            required
            rows={8}
            placeholder={
              '{"endpoints":{"products":"api/products","customers":"api/customers","orders":"api/orders"}}'
            }
          />
        </div>
        <div className="field full">
          <label>Webhook signing secret</label>
          <input name="webhook_secret" type="password" autoComplete="off" />
        </div>
      </details>
    </div>
  );
}

function formatSyncDuration(run: CommerceSyncRun) {
  if (!run.started_at) return "not started";
  if (!run.completed_at) return "in progress";
  const seconds = Math.max(
    0,
    Math.round(
      (Date.parse(run.completed_at) - Date.parse(run.started_at)) / 1000,
    ),
  );
  return seconds < 60
    ? `${seconds}s`
    : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function CatalogLoadingState() {
  return (
    <div className="catalog-loading" aria-label="Loading catalog">
      {[0, 1, 2, 3].map((row) => (
        <div key={row}>
          <span />
          <span />
          <span />
          <span />
        </div>
      ))}
    </div>
  );
}

function CatalogEmptyState({
  archived,
  onUpload,
  onPaste,
  onManual,
}: {
  archived: boolean;
  onUpload: () => void;
  onPaste: () => void;
  onManual: () => void;
}) {
  if (archived) {
    return (
      <EmptyState
        className="catalog-state-panel"
        icon={<Archive />}
        title="No archived items"
        description="Archived products and services will appear here."
      />
    );
  }
  return (
    <div className="catalog-empty-state">
      <EmptyState
        compact
        icon={<Package />}
        title="Add products & services"
        description="Import hundreds at once, paste a simple list, or add a small catalog manually."
        action={
          <Button variant="green" onClick={onUpload}>
            <FileSpreadsheet /> Upload CSV / Excel
          </Button>
        }
        secondaryAction={
          <>
            <Button variant="soft" onClick={onPaste}>
              <ClipboardPaste /> Paste a list
            </Button>
            <Button variant="tertiary" onClick={onManual}>
              <Plus /> Add manually
            </Button>
          </>
        }
      />
      <div className="catalog-integration-cta">
        <div>
          <strong>Want to sync a storefront?</strong>
          <span>
            Provider setup is optional and does not block catalog import.
          </span>
        </div>
        <Badge>Provider configuration required</Badge>
        <Link href="/integrations" className="btn btn-sm btn-secondary">
          View integrations
        </Link>
      </div>
    </div>
  );
}
