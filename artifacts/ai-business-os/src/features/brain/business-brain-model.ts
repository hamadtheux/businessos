import type {
  BusinessKnowledgeCategory,
  BusinessKnowledgeEntry,
  BusinessKnowledgeEntryCreate,
  BusinessKnowledgeEntryUpdate,
  BusinessKnowledgeStatus,
} from "../../services/api-types.ts";

export const MAX_KNOWLEDGE_TITLE_LENGTH = 250;
export const MAX_KNOWLEDGE_CONTENT_LENGTH = 50_000;

export const KNOWLEDGE_CATEGORIES: ReadonlyArray<{
  value: BusinessKnowledgeCategory;
  label: string;
}> = [
  { value: "general", label: "General" },
  { value: "faq", label: "FAQ" },
  { value: "policy", label: "Policy" },
  { value: "procedure", label: "Procedure" },
  { value: "brand", label: "Brand" },
  { value: "sales", label: "Sales" },
  { value: "support", label: "Support" },
  { value: "operations", label: "Operations" },
  { value: "marketing", label: "Marketing" },
];

export const KNOWLEDGE_EDITOR_STATUSES: ReadonlyArray<{
  value: Exclude<BusinessKnowledgeStatus, "archived">;
  label: string;
}> = [
  { value: "active", label: "Active" },
  { value: "draft", label: "Draft" },
];

export type BusinessKnowledgeDraft = {
  category: BusinessKnowledgeCategory;
  title: string;
  content: string;
  status: Exclude<BusinessKnowledgeStatus, "archived">;
};

export type BusinessKnowledgeFieldErrors = Partial<
  Record<keyof BusinessKnowledgeDraft, string>
>;

export function createBusinessKnowledgeDraft(
  entry?: BusinessKnowledgeEntry,
): BusinessKnowledgeDraft {
  return {
    category: entry?.category ?? "general",
    title: entry?.title ?? "",
    content: entry?.content ?? "",
    status: entry?.status === "draft" ? "draft" : "active",
  };
}

export function validateBusinessKnowledgeDraft(
  draft: BusinessKnowledgeDraft,
): BusinessKnowledgeFieldErrors {
  const errors: BusinessKnowledgeFieldErrors = {};
  const title = draft.title.trim();
  const content = draft.content.trim();
  if (!title) errors.title = "Add a title to continue.";
  else if (title.length > MAX_KNOWLEDGE_TITLE_LENGTH) {
    errors.title = "Keep the title to 250 characters or fewer.";
  }
  if (!content) errors.content = "Add knowledge content to continue.";
  else if (content.length > MAX_KNOWLEDGE_CONTENT_LENGTH) {
    errors.content = "Keep the content to 50,000 characters or fewer.";
  }
  return errors;
}

export function knowledgeCreateFromDraft(
  draft: BusinessKnowledgeDraft,
): BusinessKnowledgeEntryCreate {
  return {
    category: draft.category,
    title: draft.title.trim(),
    content: draft.content.trim(),
    status: draft.status,
  };
}

export function knowledgeUpdateFromDraft(
  entry: BusinessKnowledgeEntry,
  draft: BusinessKnowledgeDraft,
): BusinessKnowledgeEntryUpdate {
  const update: BusinessKnowledgeEntryUpdate = {};
  const title = draft.title.trim();
  const content = draft.content.trim();
  if (draft.category !== entry.category) update.category = draft.category;
  if (title !== entry.title) update.title = title;
  if (content !== entry.content) update.content = content;
  if (draft.status !== entry.status) update.status = draft.status;
  return update;
}

export function isCurrentBusinessBrainResponse(
  requestedBusinessId: string,
  requestedVersion: number,
  activeBusinessId: string,
  currentVersion: number,
) {
  return (
    requestedBusinessId === activeBusinessId &&
    requestedVersion === currentVersion
  );
}

export function filterBusinessKnowledge(
  entries: BusinessKnowledgeEntry[],
  query: string,
) {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return entries;
  return entries.filter(
    (entry) =>
      entry.title.toLocaleLowerCase().includes(normalized) ||
      entry.content.toLocaleLowerCase().includes(normalized),
  );
}

export function formatKnowledgeCategory(category: BusinessKnowledgeCategory) {
  return (
    KNOWLEDGE_CATEGORIES.find((option) => option.value === category)?.label ??
    category
  );
}
