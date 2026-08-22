import type { AuditEvent, WorkspaceData } from "@/types/workspace";
import { createWorkspaceSeed } from "../mocks/workspace-seeds.ts";

const VERSION = 3;
const memory = new Map<string, WorkspaceData>();
const clientEnvironment = (
  import.meta as ImportMeta & {
    readonly env?: { DEV?: boolean; VITE_ENABLE_DEMO_DATA?: string };
  }
).env;

export const demoWorkspaceDataEnabled = Boolean(
  clientEnvironment?.DEV && clientEnvironment.VITE_ENABLE_DEMO_DATA === "true",
);

function key(businessId: string) {
  return `ai-business-os:workspace:${businessId}:v${VERSION}`;
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function hydrate(value: WorkspaceData, industry: string): WorkspaceData {
  if (!demoWorkspaceDataEnabled) {
    const empty = createEmptyWorkspace();
    return {
      ...empty,
      ...value,
      catalog: empty.catalog,
      brainSources: empty.brainSources,
      analytics: { ...empty.analytics, ...value.analytics },
    };
  }
  const seed = createWorkspaceSeed(industry);
  const storedPosts = Array.isArray(value.socialPosts) ? value.socialPosts : [];
  const socialPosts = [
    ...storedPosts.map((post) => ({
      ...seed.socialPosts.find((seedPost) => seedPost.id === post.id),
      ...post,
    })),
    ...seed.socialPosts.filter(
      (seedPost) => !storedPosts.some((post) => post.id === seedPost.id),
    ),
  ];

  return { ...seed, ...value, catalog: seed.catalog, socialPosts };
}

function withoutCatalogAuthority(value: WorkspaceData): WorkspaceData {
  return {
    ...value,
    catalog: createEmptyWorkspace().catalog,
    brainSources: demoWorkspaceDataEnabled ? value.brainSources : [],
  };
}

function createEmptyWorkspace(): WorkspaceData {
  return {
    catalog: {
      method: "skip",
      sourceName: "",
      storeProvider: null,
      confirmedAt: "",
      items: [],
    },
    customers: [],
    orders: [],
    leads: [],
    conversations: [],
    agents: [],
    agentActivity: [],
    approvals: [],
    opportunities: [],
    notifications: [],
    audit: [],
    competitors: [],
    trends: [],
    campaigns: [],
    socialPosts: [],
    integrations: [],
    brainSources: [],
    workflows: [],
    analytics: {
      revenue: 0,
      orders: 0,
      customers: 0,
      leads: 0,
      conversion: 0,
      averageOrder: 0,
      repeatCustomers: 0,
      revenueSeries: [],
      acquisition: [],
    },
  };
}

function initialWorkspace(industry: string): WorkspaceData {
  return demoWorkspaceDataEnabled
    ? createWorkspaceSeed(industry)
    : createEmptyWorkspace();
}

export const workspaceRepository = {
  get(businessId: string, industry: string): WorkspaceData {
    if (industry === "__loading__") return createEmptyWorkspace();
    const cached = memory.get(businessId);
    if (cached) {
      return demoWorkspaceDataEnabled
        ? cached
        : withoutCatalogAuthority(cached);
    }
    try {
      const stored = localStorage.getItem(key(businessId));
      if (stored) {
        const value = hydrate(JSON.parse(stored) as WorkspaceData, industry);
        if (!demoWorkspaceDataEnabled) {
          localStorage.setItem(key(businessId), JSON.stringify(value));
        }
        memory.set(businessId, value);
        return value;
      }
    } catch {
      // Ignore unreadable local UI-draft data.
    }
    const initial = initialWorkspace(industry);
    memory.set(businessId, initial);
    return initial;
  },

  set(businessId: string, value: WorkspaceData) {
    const safeValue = withoutCatalogAuthority(value);
    memory.set(businessId, safeValue);
    try {
      localStorage.setItem(key(businessId), JSON.stringify(safeValue));
    } catch {
      // Local UI drafts remain optional if browser storage is unavailable.
    }
    window.dispatchEvent(
      new CustomEvent("ai-business-os:workspace-change", {
        detail: businessId,
      }),
    );
  },

  setOrThrow(businessId: string, value: WorkspaceData) {
    const safeValue = withoutCatalogAuthority(value);
    try {
      localStorage.setItem(key(businessId), JSON.stringify(safeValue));
    } catch (error) {
      const reason =
        error instanceof Error && error.message
          ? ` ${error.message}`
          : " Browser storage is unavailable.";
      throw new Error(`We couldn't save the workspace.${reason}`);
    }

    memory.set(businessId, safeValue);
    window.dispatchEvent(
      new CustomEvent("ai-business-os:workspace-change", {
        detail: businessId,
      }),
    );
  },

  update(
    businessId: string,
    industry: string,
    updater: (current: WorkspaceData) => WorkspaceData,
  ) {
    const next = updater(clone(this.get(businessId, industry)));
    this.set(businessId, next);
    return next;
  },

  updateOrThrow(
    businessId: string,
    industry: string,
    updater: (current: WorkspaceData) => WorkspaceData,
  ) {
    const next = updater(clone(this.get(businessId, industry)));
    this.setOrThrow(businessId, next);
    return next;
  },

  addAudit(
    businessId: string,
    industry: string,
    event: Omit<AuditEvent, "id" | "timestamp">,
  ) {
    return this.update(businessId, industry, (current) => ({
      ...current,
      audit: [
        { id: `audit-${Date.now()}`, timestamp: "Just now", ...event },
        ...current.audit,
      ],
    }));
  },
};
