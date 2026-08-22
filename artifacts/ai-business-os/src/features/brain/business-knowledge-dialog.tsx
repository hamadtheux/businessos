import { useState, type FormEvent } from "react";
import { AlertCircle, Check, RefreshCw } from "lucide-react";
import { Badge, Button, Modal } from "@/components/product-ui";
import {
  KNOWLEDGE_CATEGORIES,
  KNOWLEDGE_EDITOR_STATUSES,
  MAX_KNOWLEDGE_CONTENT_LENGTH,
  MAX_KNOWLEDGE_TITLE_LENGTH,
  createBusinessKnowledgeDraft,
  knowledgeCreateFromDraft,
  knowledgeUpdateFromDraft,
  validateBusinessKnowledgeDraft,
  type BusinessKnowledgeDraft,
  type BusinessKnowledgeFieldErrors,
} from "./business-brain-model";
import {
  businessBrainApi,
  humanizeBusinessBrainError,
  knowledgeValidationFields,
} from "@/services/business-brain";
import type { BusinessKnowledgeEntry } from "@/services/api-types";

export function BusinessKnowledgeDialog({
  businessId,
  businessName,
  entry,
  onClose,
  onSaved,
}: {
  businessId: string;
  businessName: string;
  entry?: BusinessKnowledgeEntry;
  onClose: () => void;
  onSaved: (entry: BusinessKnowledgeEntry) => void;
}) {
  const [draft, setDraft] = useState<BusinessKnowledgeDraft>(() =>
    createBusinessKnowledgeDraft(entry),
  );
  const [fieldErrors, setFieldErrors] = useState<BusinessKnowledgeFieldErrors>(
    {},
  );
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const update = <K extends keyof BusinessKnowledgeDraft>(
    field: K,
    value: BusinessKnowledgeDraft[K],
  ) => {
    setDraft((current) => ({ ...current, [field]: value }));
    setFieldErrors((current) => ({ ...current, [field]: undefined }));
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const validationErrors = validateBusinessKnowledgeDraft(draft);
    if (Object.keys(validationErrors).length) {
      setFieldErrors(validationErrors);
      setError("Review the highlighted fields and try again.");
      return;
    }

    setSaving(true);
    setError("");
    setFieldErrors({});
    try {
      if (entry) {
        const changes = knowledgeUpdateFromDraft(entry, draft);
        if (Object.keys(changes).length === 0) {
          onSaved(entry);
          return;
        }
        onSaved(
          await businessBrainApi.updateKnowledge(businessId, entry.id, changes),
        );
      } else {
        onSaved(
          await businessBrainApi.createKnowledge(
            businessId,
            knowledgeCreateFromDraft(draft),
          ),
        );
      }
    } catch (reason) {
      setFieldErrors(knowledgeValidationFields(reason));
      setError(
        humanizeBusinessBrainError(
          reason,
          `We couldn't ${entry ? "save" : "add"} this knowledge. Please try again.`,
        ),
      );
    } finally {
      setSaving(false);
    }
  };

  const contentCountTone = draft.content.length >= 45_000 ? "near-limit" : "";

  return (
    <Modal
      title={entry ? "Edit knowledge" : "Add knowledge"}
      description={`Saved directly to ${businessName}'s Business Brain.`}
      onClose={onClose}
      wide
    >
      <form onSubmit={submit} data-testid="business-knowledge-form">
        <div className="form-grid brain-form-grid">
          <div className="field">
            <label htmlFor="knowledge-category">Category</label>
            <select
              id="knowledge-category"
              value={draft.category}
              onChange={(event) =>
                update(
                  "category",
                  event.target.value as BusinessKnowledgeDraft["category"],
                )
              }
              aria-invalid={Boolean(fieldErrors.category)}
              data-testid="select-knowledge-category"
            >
              {KNOWLEDGE_CATEGORIES.map((category) => (
                <option value={category.value} key={category.value}>
                  {category.label}
                </option>
              ))}
            </select>
            {fieldErrors.category && (
              <span className="field-error">{fieldErrors.category}</span>
            )}
          </div>
          <div className="field">
            <label htmlFor="knowledge-status">Status</label>
            <select
              id="knowledge-status"
              value={draft.status}
              onChange={(event) =>
                update(
                  "status",
                  event.target.value as BusinessKnowledgeDraft["status"],
                )
              }
              aria-invalid={Boolean(fieldErrors.status)}
              data-testid="select-knowledge-status"
            >
              {KNOWLEDGE_EDITOR_STATUSES.map((status) => (
                <option value={status.value} key={status.value}>
                  {status.label}
                </option>
              ))}
            </select>
            {fieldErrors.status && (
              <span className="field-error">{fieldErrors.status}</span>
            )}
          </div>
          <div className="field full">
            <label htmlFor="knowledge-title">Title</label>
            <input
              id="knowledge-title"
              autoFocus
              value={draft.title}
              onChange={(event) => update("title", event.target.value)}
              maxLength={MAX_KNOWLEDGE_TITLE_LENGTH}
              aria-invalid={Boolean(fieldErrors.title)}
              placeholder="For example, Returns policy"
              data-testid="input-knowledge-title"
            />
            <div className="brain-field-meta">
              {fieldErrors.title ? (
                <span className="field-error">{fieldErrors.title}</span>
              ) : (
                <span>Use a clear name your team will recognize.</span>
              )}
              <span>
                {draft.title.length}/{MAX_KNOWLEDGE_TITLE_LENGTH}
              </span>
            </div>
          </div>
          <div className="field full">
            <label htmlFor="knowledge-content">Content</label>
            <textarea
              id="knowledge-content"
              value={draft.content}
              onChange={(event) => update("content", event.target.value)}
              maxLength={MAX_KNOWLEDGE_CONTENT_LENGTH}
              rows={13}
              aria-invalid={Boolean(fieldErrors.content)}
              placeholder="Add the policy, answer, procedure, or guidance in plain text."
              data-testid="textarea-knowledge-content"
            />
            <div className={`brain-field-meta ${contentCountTone}`}>
              {fieldErrors.content ? (
                <span className="field-error">{fieldErrors.content}</span>
              ) : (
                <span>Plain text, with line breaks where helpful.</span>
              )}
              <span>
                {draft.content.length.toLocaleString()}/
                {MAX_KNOWLEDGE_CONTENT_LENGTH.toLocaleString()}
              </span>
            </div>
          </div>
        </div>
        {entry && (
          <div className="brain-source-readonly">
            <span>Source</span>
            <Badge tone={entry.source_type === "manual" ? "neutral" : "info"}>
              {entry.source_type === "manual" ? "Manual" : "System"}
            </Badge>
            <p>
              Source details are managed by the system and cannot be edited.
            </p>
          </div>
        )}
        {error && (
          <div className="catalog-inline-error" role="alert">
            <AlertCircle /> {error}
          </div>
        )}
        <div className="modal-foot">
          <Button type="button" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button
            variant="primary"
            type="submit"
            disabled={saving}
            data-testid="button-save-knowledge"
          >
            {saving ? (
              <>
                <RefreshCw className="spin" /> Saving…
              </>
            ) : (
              <>
                <Check /> {entry ? "Save changes" : "Add knowledge"}
              </>
            )}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
