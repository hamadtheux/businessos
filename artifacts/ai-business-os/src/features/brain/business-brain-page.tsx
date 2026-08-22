import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  AlertCircle,
  Archive,
  BookOpen,
  Brain,
  Building2,
  Check,
  Package,
  Palette,
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
  Modal,
  PageHeader,
} from "@/components/product-ui";
import {
  businessBrainApi,
  humanizeBusinessBrainError,
} from "@/services/business-brain";
import type {
  BusinessBrainManifest,
  BusinessKnowledgeCategory,
  BusinessKnowledgeEntry,
  BusinessKnowledgeStatus,
} from "@/services/api-types";
import { BusinessKnowledgeDialog } from "./business-knowledge-dialog";
import {
  KNOWLEDGE_CATEGORIES,
  filterBusinessKnowledge,
  formatKnowledgeCategory,
  isCurrentBusinessBrainResponse,
} from "./business-brain-model";

type LoadState = "loading" | "success" | "error";
type StatusFilter = "default" | BusinessKnowledgeStatus;

export function BusinessBrainPage() {
  const { activeBusiness, activeBusinessId } = useBusiness();
  const [entries, setEntries] = useState<BusinessKnowledgeEntry[]>([]);
  const [manifest, setManifest] = useState<BusinessBrainManifest | null>(null);
  const [knowledgeLoadState, setKnowledgeLoadState] =
    useState<LoadState>("loading");
  const [manifestLoadState, setManifestLoadState] =
    useState<LoadState>("loading");
  const [loadError, setLoadError] = useState("");
  const [category, setCategory] = useState<"all" | BusinessKnowledgeCategory>(
    "all",
  );
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("default");
  const [query, setQuery] = useState("");
  const [editor, setEditor] = useState<
    "create" | BusinessKnowledgeEntry | null
  >(null);
  const [archiveEntry, setArchiveEntry] =
    useState<BusinessKnowledgeEntry | null>(null);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [reloadVersion, setReloadVersion] = useState(0);
  const requestVersion = useRef(0);
  const activeBusinessIdRef = useRef(activeBusinessId);
  activeBusinessIdRef.current = activeBusinessId;

  useEffect(() => {
    setEntries([]);
    setManifest(null);
    setEditor(null);
    setArchiveEntry(null);
    setArchiveBusy(false);
    setActionError("");
    setNotice("");
    setQuery("");
  }, [activeBusinessId]);

  useEffect(() => {
    const businessId = activeBusinessId;
    const version = ++requestVersion.current;
    const controller = new AbortController();
    setEntries([]);
    setManifest(null);
    setKnowledgeLoadState("loading");
    setManifestLoadState("loading");
    setLoadError("");

    if (!businessId) {
      setKnowledgeLoadState("success");
      setManifestLoadState("success");
      return () => controller.abort();
    }

    const knowledgeRequest = businessBrainApi.listKnowledge(
      businessId,
      {
        category: category === "all" ? undefined : category,
        status: statusFilter === "default" ? undefined : statusFilter,
      },
      controller.signal,
    );
    const manifestRequest = businessBrainApi.getManifest(
      businessId,
      controller.signal,
    );

    void Promise.allSettled([knowledgeRequest, manifestRequest]).then(
      ([knowledgeResult, manifestResult]) => {
        if (
          controller.signal.aborted ||
          !isCurrentBusinessBrainResponse(
            businessId,
            version,
            activeBusinessIdRef.current,
            requestVersion.current,
          )
        ) {
          return;
        }

        if (knowledgeResult.status === "fulfilled") {
          setEntries(
            knowledgeResult.value.filter(
              (entry) => entry.business_id === businessId,
            ),
          );
          setKnowledgeLoadState("success");
        } else {
          setLoadError(
            humanizeBusinessBrainError(
              knowledgeResult.reason,
              "We couldn't load this business's knowledge. Please try again.",
            ),
          );
          setKnowledgeLoadState("error");
        }

        if (
          manifestResult.status === "fulfilled" &&
          manifestResult.value.business_id === businessId
        ) {
          setManifest(manifestResult.value);
          setManifestLoadState("success");
        } else {
          setManifest(null);
          setManifestLoadState("error");
        }
      },
    );

    return () => controller.abort();
  }, [activeBusinessId, category, reloadVersion, statusFilter]);

  const visibleEntries = useMemo(
    () => filterBusinessKnowledge(entries, query),
    [entries, query],
  );

  const reload = () => setReloadVersion((current) => current + 1);

  const handleSaved = (savedEntry: BusinessKnowledgeEntry) => {
    if (savedEntry.business_id !== activeBusinessIdRef.current) return;
    setEntries((current) => {
      const exists = current.some((entry) => entry.id === savedEntry.id);
      return exists
        ? current.map((entry) =>
            entry.id === savedEntry.id ? savedEntry : entry,
          )
        : [...current, savedEntry];
    });
    setEditor(null);
    setNotice("Knowledge saved and available to this Business Brain.");
    reload();
  };

  const archiveSelected = async () => {
    if (!archiveEntry) return;
    const businessId = archiveEntry.business_id;
    const entryId = archiveEntry.id;
    setArchiveBusy(true);
    setActionError("");
    try {
      await businessBrainApi.archiveKnowledge(businessId, entryId);
      if (businessId === activeBusinessIdRef.current) {
        setEntries((current) =>
          current.filter((entry) => entry.id !== entryId),
        );
        setArchiveEntry(null);
        setNotice("Knowledge archived. Its historical record is preserved.");
        reload();
      }
    } catch (reason) {
      if (businessId === activeBusinessIdRef.current) {
        setActionError(
          humanizeBusinessBrainError(
            reason,
            "We couldn't archive this knowledge. Please try again.",
          ),
        );
      }
    } finally {
      if (businessId === activeBusinessIdRef.current) setArchiveBusy(false);
    }
  };

  const restoreEntry = async (entry: BusinessKnowledgeEntry) => {
    const businessId = entry.business_id;
    setActionError("");
    try {
      const restored = await businessBrainApi.updateKnowledge(
        businessId,
        entry.id,
        { status: "active" },
      );
      if (restored.business_id === activeBusinessIdRef.current) {
        setNotice("Knowledge restored to Active.");
        reload();
      }
    } catch (reason) {
      if (businessId === activeBusinessIdRef.current) {
        setActionError(
          humanizeBusinessBrainError(
            reason,
            "We couldn't restore this knowledge. Please try again.",
          ),
        );
      }
    }
  };

  const sourceCount = manifest?.source_count;
  const isUnfilteredCurrentView =
    category === "all" && statusFilter === "default";

  return (
    <>
      <PageHeader
        eyebrow="Knowledge layer"
        title="Business Brain"
        subtitle={`Authoritative business context for ${activeBusiness?.name ?? "this business"}.`}
        action={
          <Button
            variant="primary"
            onClick={() => setEditor("create")}
            disabled={!activeBusiness}
            data-testid="button-add-knowledge"
          >
            <Plus /> Add knowledge
          </Button>
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

      <Card className="brain-source-overview">
        <div className="brain-overview-head">
          <div>
            <div className="eyebrow">Connected sources</div>
            <h2>
              {manifestLoadState === "loading"
                ? "Checking Business Brain sources…"
                : sourceCount === undefined
                  ? "Source status unavailable"
                  : `${sourceCount.toLocaleString()} ${sourceCount === 1 ? "source" : "sources"} available`}
            </h2>
          </div>
          <Badge tone={manifest ? "success" : "neutral"}>
            {manifest ? "Authoritative" : "Not available"}
          </Badge>
        </div>
        <div className="brain-source-grid" data-testid="brain-source-counts">
          <BrainSourceMetric
            icon={<Building2 />}
            label="Business profile"
            description="Automatic"
            count={manifest?.source_counts_by_type.business_profile}
            loading={manifestLoadState === "loading"}
          />
          <BrainSourceMetric
            icon={<Palette />}
            label="Brand"
            description="Automatic"
            count={manifest?.source_counts_by_type.branding}
            loading={manifestLoadState === "loading"}
          />
          <BrainSourceMetric
            icon={<Package />}
            label="Products & services"
            description="From catalog"
            count={manifest?.source_counts_by_type.catalog_item}
            loading={manifestLoadState === "loading"}
          />
          <BrainSourceMetric
            icon={<BookOpen />}
            label="Curated knowledge"
            description="Managed here"
            count={manifest?.source_counts_by_type.knowledge_entry}
            loading={manifestLoadState === "loading"}
          />
        </div>
      </Card>

      <Card className="brain-knowledge-card" pad={false}>
        <div className="table-toolbar brain-toolbar">
          <div>
            <div className="eyebrow">Curated knowledge</div>
            <h2>
              {knowledgeLoadState === "loading"
                ? "Loading knowledge…"
                : `${entries.length} ${statusFilter === "archived" ? "archived" : "knowledge"} ${entries.length === 1 ? "entry" : "entries"}`}
            </h2>
          </div>
          <div className="brain-filter-bar">
            <div className="search-box brain-knowledge-search">
              <Search />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search loaded knowledge"
                aria-label="Search loaded Business Brain knowledge"
                data-testid="input-search-knowledge"
              />
            </div>
            <select
              value={category}
              onChange={(event) =>
                setCategory(
                  event.target.value as "all" | BusinessKnowledgeCategory,
                )
              }
              aria-label="Filter knowledge category"
              data-testid="select-filter-knowledge-category"
            >
              <option value="all">All categories</option>
              {KNOWLEDGE_CATEGORIES.map((option) => (
                <option value={option.value} key={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <select
              value={statusFilter}
              onChange={(event) =>
                setStatusFilter(event.target.value as StatusFilter)
              }
              aria-label="Filter knowledge status"
              data-testid="select-filter-knowledge-status"
            >
              <option value="default">Current</option>
              <option value="active">Active</option>
              <option value="draft">Draft</option>
              <option value="archived">Archived</option>
            </select>
          </div>
        </div>

        {knowledgeLoadState === "loading" && <KnowledgeLoadingState />}

        {knowledgeLoadState === "error" && (
          <div className="empty brain-state-panel">
            <AlertCircle />
            <h3>We couldn't load this business's knowledge</h3>
            <p>{loadError}</p>
            <Button variant="green" onClick={reload}>
              <RefreshCw /> Try again
            </Button>
          </div>
        )}

        {knowledgeLoadState === "success" && entries.length === 0 && (
          <KnowledgeEmptyState
            archived={statusFilter === "archived"}
            filtered={!isUnfilteredCurrentView}
            onAdd={() => setEditor("create")}
          />
        )}

        {knowledgeLoadState === "success" && entries.length > 0 && (
          <div className="brain-entry-list" data-testid="knowledge-entry-list">
            {visibleEntries.map((entry) => (
              <article className="brain-entry-row" key={entry.id}>
                <div className="source-icon">
                  <BookOpen />
                </div>
                <div className="brain-entry-main">
                  <div className="brain-entry-title-line">
                    <h3>{entry.title}</h3>
                    <Badge
                      tone={
                        entry.status === "active"
                          ? "success"
                          : entry.status === "archived"
                            ? "neutral"
                            : "warning"
                      }
                    >
                      {entry.status}
                    </Badge>
                  </div>
                  <p className="brain-entry-content">{entry.content}</p>
                  <div className="brain-entry-meta">
                    <span>{formatKnowledgeCategory(entry.category)}</span>
                    <span>•</span>
                    <span>
                      {entry.source_type === "manual" ? "Manual" : "System"}
                    </span>
                    <span>•</span>
                    <span>Updated {formatKnowledgeDate(entry.updated_at)}</span>
                  </div>
                </div>
                <div className="brain-entry-actions">
                  {entry.status === "archived" ? (
                    <button
                      className="icon-btn"
                      aria-label={`Restore ${entry.title}`}
                      onClick={() => void restoreEntry(entry)}
                    >
                      <RotateCcw />
                    </button>
                  ) : (
                    <>
                      <button
                        className="icon-btn"
                        aria-label={`Edit ${entry.title}`}
                        onClick={() => setEditor(entry)}
                      >
                        <Pencil />
                      </button>
                      <button
                        className="icon-btn danger-icon-btn"
                        aria-label={`Archive ${entry.title}`}
                        onClick={() => setArchiveEntry(entry)}
                      >
                        <Archive />
                      </button>
                    </>
                  )}
                </div>
              </article>
            ))}
            {visibleEntries.length === 0 && (
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
        <BusinessKnowledgeDialog
          key={`${activeBusiness.id}-${editor === "create" ? "create" : editor.id}`}
          businessId={activeBusiness.id}
          businessName={activeBusiness.name}
          entry={editor === "create" ? undefined : editor}
          onClose={() => setEditor(null)}
          onSaved={handleSaved}
        />
      )}

      {archiveEntry && (
        <Modal
          title="Archive knowledge?"
          description="This removes the entry from current knowledge while preserving its historical record."
          onClose={() => setArchiveEntry(null)}
        >
          <p className="catalog-confirm-copy">
            Archive <strong>{archiveEntry.title}</strong>? You can find and
            restore it later using the Archived filter.
          </p>
          {actionError && (
            <div className="catalog-inline-error" role="alert">
              <AlertCircle /> {actionError}
            </div>
          )}
          <div className="modal-foot">
            <Button
              onClick={() => setArchiveEntry(null)}
              disabled={archiveBusy}
            >
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() => void archiveSelected()}
              disabled={archiveBusy}
              data-testid="button-confirm-archive-knowledge"
            >
              {archiveBusy ? <RefreshCw className="spin" /> : <Archive />}
              Archive knowledge
            </Button>
          </div>
        </Modal>
      )}
    </>
  );
}

function BrainSourceMetric({
  icon,
  label,
  description,
  count,
  loading,
}: {
  icon: ReactNode;
  label: string;
  description: string;
  count?: number;
  loading: boolean;
}) {
  return (
    <div className="brain-source-metric">
      <div className="source-icon">{icon}</div>
      <div>
        <strong>{label}</strong>
        <span>{description}</span>
      </div>
      <b>{loading ? "—" : (count ?? "Unavailable")}</b>
    </div>
  );
}

function KnowledgeLoadingState() {
  return (
    <div
      className="brain-loading"
      aria-label="Loading Business Brain knowledge"
    >
      {[0, 1, 2].map((row) => (
        <div key={row}>
          <span />
          <span />
          <span />
        </div>
      ))}
    </div>
  );
}

function KnowledgeEmptyState({
  archived,
  filtered,
  onAdd,
}: {
  archived: boolean;
  filtered: boolean;
  onAdd: () => void;
}) {
  if (archived) {
    return (
      <div className="empty brain-state-panel">
        <Archive />
        <h3>No archived knowledge</h3>
        <p>Archived entries will appear here and can be restored.</p>
      </div>
    );
  }
  if (filtered) {
    return (
      <div className="empty brain-state-panel">
        <BookOpen />
        <h3>No knowledge matches these filters</h3>
        <p>Try another category or status.</p>
      </div>
    );
  }
  return (
    <div className="empty brain-empty-state">
      <Brain />
      <h3>Add curated business knowledge</h3>
      <p>
        Your Business Brain already uses your business profile, branding, and
        products or services. Add knowledge to teach it policies, FAQs, and
        operating details.
      </p>
      <Button variant="primary" onClick={onAdd}>
        <Plus /> Add knowledge
      </Button>
      <div
        className="brain-suggested-categories"
        aria-label="Suggested categories"
      >
        {KNOWLEDGE_CATEGORIES.filter(
          (option) => option.value !== "general",
        ).map((option) => (
          <span key={option.value}>{option.label}</span>
        ))}
      </div>
    </div>
  );
}

function formatKnowledgeDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "recently";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
  }).format(date);
}
