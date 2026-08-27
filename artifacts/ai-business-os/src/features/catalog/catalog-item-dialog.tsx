import { useState, type FormEvent } from "react";
import { AlertCircle, Check, RefreshCw } from "lucide-react";
import { Button, Modal } from "@/components/product-ui";
import {
  catalogCreateFromDraft,
  catalogUpdateFromDraft,
  createCatalogItemDraft,
  validateCatalogItemDraft,
  type CatalogItemDraft,
} from "./catalog-model";
import { catalogApi, humanizeCatalogError } from "@/services/catalog";
import type { CatalogItem, CatalogItemType } from "@/services/api-types";

export function CatalogItemDialog({
  businessId,
  businessName,
  currency,
  item,
  onClose,
  onSaved,
}: {
  businessId: string;
  businessName: string;
  currency: string;
  item?: CatalogItem;
  onClose: () => void;
  onSaved: (item: CatalogItem) => void;
}) {
  const [draft, setDraft] = useState<CatalogItemDraft>(() =>
    createCatalogItemDraft(item),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const update = <K extends keyof CatalogItemDraft>(
    field: K,
    value: CatalogItemDraft[K],
  ) => setDraft((current) => ({ ...current, [field]: value }));

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const validationError = validateCatalogItemDraft(draft);
    if (validationError) {
      setError(validationError);
      return;
    }
    setSaving(true);
    setError("");
    try {
      if (item) {
        const changes = catalogUpdateFromDraft(item, draft);
        if (Object.keys(changes).length === 0) {
          onSaved(item);
          return;
        }
        onSaved(
          await catalogApi.updateCatalogItem(businessId, item.id, changes),
        );
      } else {
        onSaved(
          await catalogApi.createCatalogItem(
            businessId,
            catalogCreateFromDraft(draft),
          ),
        );
      }
    } catch (reason) {
      setError(
        humanizeCatalogError(
          reason,
          `We couldn't ${item ? "save" : "add"} this item. Please try again.`,
        ),
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      title={item ? "Edit catalog item" : "Add product or service"}
      description={`Saved directly to ${businessName}'s catalog.`}
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <div
          className="catalog-type-choice"
          role="group"
          aria-label="Item type"
        >
          {(["product", "service"] as CatalogItemType[]).map((itemType) => (
            <button
              key={itemType}
              type="button"
              className={draft.itemType === itemType ? "active" : ""}
              onClick={() => update("itemType", itemType)}
            >
              {itemType === "product" ? "Product" : "Service"}
            </button>
          ))}
        </div>
        <div className="form-grid catalog-form-grid">
          <div className="field full">
            <label>Name</label>
            <input
              autoFocus
              value={draft.name}
              onChange={(event) => update("name", event.target.value)}
              maxLength={200}
              required
              data-testid="input-catalog-item-name"
            />
          </div>
          <div className="field">
            <label>SKU</label>
            <input
              value={draft.sku}
              onChange={(event) => update("sku", event.target.value)}
              onBlur={() => update("sku", draft.sku.trim().toUpperCase())}
              maxLength={100}
              placeholder="Optional"
              data-testid="input-catalog-item-sku"
            />
          </div>
          <div className="field">
            <label>Price</label>
            <input
              inputMode="decimal"
              value={draft.price}
              onChange={(event) => update("price", event.target.value)}
              placeholder="Optional"
              data-testid="input-catalog-item-price"
            />
          </div>
          <div className="field">
            <label>Status</label>
            <select
              value={draft.status}
              onChange={(event) =>
                update(
                  "status",
                  event.target.value as CatalogItemDraft["status"],
                )
              }
            >
              <option value="active">Active</option>
              <option value="draft">Draft</option>
              <option value="archived">Archived</option>
            </select>
          </div>
          <div className="field full">
            <label>Description</label>
            <textarea
              value={draft.description}
              onChange={(event) => update("description", event.target.value)}
              maxLength={10_000}
              placeholder="Optional details customers or your team should know"
              data-testid="textarea-catalog-item-description"
            />
          </div>
          {draft.itemType === "product" && <details className="field full">
            <summary><strong>Commerce details</strong> · inventory, feed quality, and product grounding</summary>
            <div className="form-grid section-gap">
              <div className="field"><label>Currency</label><input value={draft.currency} onChange={(event) => update("currency", event.target.value.toUpperCase())} maxLength={3} placeholder={currency} /></div>
              <div className="field"><label>Compare-at price</label><input inputMode="decimal" value={draft.compareAtPrice} onChange={(event) => update("compareAtPrice", event.target.value)} placeholder="Optional" /></div>
              <div className="field"><label>Cost</label><input inputMode="decimal" value={draft.cost} onChange={(event) => update("cost", event.target.value)} placeholder="Private unit cost" /></div>
              <div className="field"><label>Inventory quantity</label><input inputMode="numeric" value={draft.inventoryQuantity} onChange={(event) => update("inventoryQuantity", event.target.value)} placeholder="Optional" /></div>
              <div className="field"><label>Availability</label><select value={draft.availability} onChange={(event) => update("availability", event.target.value as CatalogItemDraft["availability"])}><option value="unknown">Unknown</option><option value="in_stock">In stock</option><option value="out_of_stock">Out of stock</option><option value="preorder">Preorder</option><option value="backorder">Backorder</option></select></div>
              <div className="field"><label>Condition</label><select value={draft.condition} onChange={(event) => update("condition", event.target.value as CatalogItemDraft["condition"])}><option value="new">New</option><option value="refurbished">Refurbished</option><option value="used">Used</option></select></div>
              <div className="field full"><label>Product URL</label><input type="url" value={draft.productUrl} onChange={(event) => update("productUrl", event.target.value)} maxLength={2048} placeholder="https://store.example.com/products/item" /></div>
              <div className="field"><label>Brand</label><input value={draft.brand} onChange={(event) => update("brand", event.target.value)} maxLength={160} /></div>
              <div className="field"><label>Vendor</label><input value={draft.vendor} onChange={(event) => update("vendor", event.target.value)} maxLength={160} /></div>
              <div className="field"><label>GTIN</label><input value={draft.gtin} onChange={(event) => update("gtin", event.target.value)} maxLength={32} /></div>
              <div className="field"><label>MPN</label><input value={draft.mpn} onChange={(event) => update("mpn", event.target.value)} maxLength={100} /></div>
              <div className="field full"><label>Google product category</label><input value={draft.googleProductCategory} onChange={(event) => update("googleProductCategory", event.target.value)} maxLength={255} /></div>
              <div className="field full"><label>Tags</label><input value={draft.tags} onChange={(event) => update("tags", event.target.value)} placeholder="premium, seasonal, local" /></div>
              <label className="checkbox-row full"><input type="checkbox" checked={draft.published} onChange={(event) => update("published", event.target.checked)} /> Available to customer-facing catalog experiences</label>
            </div>
          </details>}
        </div>
        {error && (
          <div className="catalog-inline-error" role="alert">
            <AlertCircle /> {error}
          </div>
        )}
        <div className="modal-foot">
          <Button type="button" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" disabled={saving}>
            {saving ? (
              <>
                <RefreshCw className="spin" /> Saving…
              </>
            ) : (
              <>
                <Check /> {item ? "Save changes" : "Add item"}
              </>
            )}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
