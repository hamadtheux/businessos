import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "wouter";
import {
  AlertCircle,
  Archive,
  Check,
  ClipboardPaste,
  FileSpreadsheet,
  Package,
  Pencil,
  Plus,
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
} from "@/components/product-ui";
import { catalogApi, humanizeCatalogError } from "@/services/catalog";
import type {
  CatalogItem,
  CatalogItemStatus,
  CatalogItemType,
} from "@/services/api-types";
import { CatalogImportDialog } from "./catalog-import-dialog";
import { CatalogItemDialog } from "./catalog-item-dialog";
import { formatCatalogPrice, isCurrentCatalogResponse } from "./catalog-model";

type LoadState = "loading" | "success" | "error";
type StatusFilter = "default" | CatalogItemStatus;

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

  return (
    <>
      <PageHeader
        eyebrow="Business catalog"
        title="Products & services"
        subtitle={`Build ${activeBusiness?.name ?? "this business"}'s catalog manually or in bulk.`}
        action={loadState === "success" && items.length > 0 ? (
          <>
            <Button variant="green" onClick={() => setImportMode("upload")}>
              <FileSpreadsheet /> Upload CSV / Excel
            </Button>
            <Button variant="soft" onClick={() => setImportMode("paste")}>
              <ClipboardPaste /> Paste a list
            </Button>
            <Button variant="tertiary" onClick={() => setEditor("create")}>
              <Plus /> Add manually
            </Button>
          </>
        ) : undefined}
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

      <Card className="table-card catalog-workspace-card" pad={false}>
        <div className="table-toolbar catalog-toolbar">
          <div>
            <div className="eyebrow">Live catalog</div>
            <h2>
              {loadState === "loading"
                ? "Loading items…"
                : `${items.length} ${statusFilter === "archived" ? "archived" : "catalog"} items`}
            </h2>
          </div>
          <div className="catalog-filter-bar">
            <div className="search-box catalog-search">
              <Search />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search loaded items"
                aria-label="Search loaded catalog items"
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
                        {item.status}
                      </Badge>
                    </td>
                    <td>
                      <div className="catalog-row-actions">
                        {item.status === "archived" ? (
                          <button
                            className="icon-btn"
                            aria-label={`Restore ${item.name}`}
                            onClick={() => void restoreItem(item)}
                          >
                            <RotateCcw />
                          </button>
                        ) : (
                          <>
                            <button
                              className="icon-btn"
                              aria-label={`Edit ${item.name}`}
                              onClick={() => setEditor(item)}
                            >
                              <Pencil />
                            </button>
                            <button
                              className="icon-btn danger-icon-btn"
                              aria-label={`Archive ${item.name}`}
                              onClick={() => setArchiveItem(item)}
                            >
                              <Archive />
                            </button>
                          </>
                        )}
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
    </>
  );
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
          <span>Provider setup is optional and does not block catalog import.</span>
        </div>
        <Badge>Provider configuration required</Badge>
        <Link href="/integrations" className="btn btn-sm btn-secondary">
          View integrations
        </Link>
      </div>
    </div>
  );
}
