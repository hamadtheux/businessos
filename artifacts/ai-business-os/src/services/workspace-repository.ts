import type { AuditEvent, WorkspaceData } from "@/types/workspace";
import { createWorkspaceSeed } from "../mocks/workspace-seeds.ts";

const VERSION = 2;
const memory = new Map<string, WorkspaceData>();

function key(businessId: string) {
  return `ai-business-os:workspace:${businessId}:v${VERSION}`;
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function hydrate(value: WorkspaceData, industry: string): WorkspaceData {
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

  return { ...seed, ...value, socialPosts };
}

export const workspaceRepository = {
  get(businessId: string, industry: string): WorkspaceData {
    if (industry === "__loading__")
      return createWorkspaceSeed("Farm/Agriculture");
    const cached = memory.get(businessId);
    if (cached) return cached;
    try {
      const stored = localStorage.getItem(key(businessId));
      if (stored) {
        const value = hydrate(JSON.parse(stored) as WorkspaceData, industry);
        memory.set(businessId, value);
        return value;
      }
    } catch {
      // Fall back to a deterministic business-aware seed.
    }
    const seeded = createWorkspaceSeed(industry);
    memory.set(businessId, seeded);
    return seeded;
  },

  set(businessId: string, value: WorkspaceData) {
    memory.set(businessId, value);
    try {
      localStorage.setItem(key(businessId), JSON.stringify(value));
    } catch {
      // The prototype remains usable if browser storage is unavailable.
    }
    window.dispatchEvent(
      new CustomEvent("ai-business-os:workspace-change", {
        detail: businessId,
      }),
    );
  },

  setOrThrow(businessId: string, value: WorkspaceData) {
    try {
      localStorage.setItem(key(businessId), JSON.stringify(value));
    } catch (error) {
      const reason =
        error instanceof Error && error.message
          ? ` ${error.message}`
          : " Browser storage is unavailable.";
      throw new Error(`We couldn't save the workspace.${reason}`);
    }

    memory.set(businessId, value);
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
