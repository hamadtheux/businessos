import { businessDraftFromInput } from "./business-model.ts";
import type { BusinessInput, BusinessLocalDraft } from "../types/business.ts";

const VERSION = 1;
const memory = new Map<string, BusinessLocalDraft>();

function key(businessId: string) {
  return `ai-business-os:business-ui-draft:${businessId}:v${VERSION}`;
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function withoutSavedBranding(draft: BusinessLocalDraft): BusinessLocalDraft {
  const { brandIdentity: _discardedBranding, ...safeDraft } =
    draft as BusinessLocalDraft & { brandIdentity?: unknown };
  return safeDraft;
}

export const businessDraftRepository = {
  get(businessId: string): BusinessLocalDraft | undefined {
    const cached = memory.get(businessId);
    if (cached) return clone(withoutSavedBranding(cached));
    try {
      const raw = localStorage.getItem(key(businessId));
      if (!raw) return undefined;
      const draft = withoutSavedBranding(JSON.parse(raw) as BusinessLocalDraft);
      memory.set(businessId, draft);
      try {
        localStorage.setItem(key(businessId), JSON.stringify(draft));
      } catch {
        // Sanitized memory state remains usable when storage is unavailable.
      }
      return clone(draft);
    } catch {
      return undefined;
    }
  },

  save(businessId: string, input: BusinessInput): BusinessLocalDraft {
    const draft = withoutSavedBranding(
      businessDraftFromInput(input, this.get(businessId)),
    );
    try {
      localStorage.setItem(key(businessId), JSON.stringify(draft));
    } catch {
      // UI drafts are best-effort and never replace PostgreSQL authority.
    }
    memory.set(businessId, clone(draft));
    return clone(draft);
  },
};
