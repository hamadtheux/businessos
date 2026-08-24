import { useState, type ChangeEvent, type DragEvent } from "react";
import {
  AlertCircle,
  Check,
  Clock3,
  FileSpreadsheet,
  Lightbulb,
  ListPlus,
  Package,
  Plug,
  Plus,
  ShoppingBag,
  UploadCloud,
  X,
} from "lucide-react";
import { Badge, Button } from "@/components/product-ui";
import {
  catalogFileValidationMessage,
  createPasteCatalogFile,
  pasteListLines,
} from "@/features/catalog/catalog-model";
import { cx } from "@/lib/product-utils";
import type { CatalogItemType } from "@/services/api-types";
import {
  createBlankCatalogProduct,
  expectedCatalogColumns,
  type CatalogDraft,
  type CatalogDraftProduct,
  type CatalogImportMethod,
} from "./catalog-import";

const importOptions: Array<{
  id: CatalogImportMethod;
  title: string;
  copy: string;
  icon: typeof ListPlus;
}> = [
  {
    id: "upload",
    title: "Upload CSV / Excel",
    copy: "Best for an existing catalog",
    icon: FileSpreadsheet,
  },
  {
    id: "paste",
    title: "Paste a list",
    copy: "One product or service per line",
    icon: Package,
  },
  {
    id: "manual",
    title: "Add manually",
    copy: "Best for a few items",
    icon: ListPlus,
  },
  {
    id: "store",
    title: "Store connection",
    copy: "Provider configuration required",
    icon: ShoppingBag,
  },
  {
    id: "skip",
    title: "Skip for now",
    copy: "Add your catalog later",
    icon: Clock3,
  },
];

export function ProductCatalogStep({
  catalog,
  selectedFile,
  onChange,
  onFileChange,
}: {
  catalog: CatalogDraft;
  selectedFile: File | null;
  onChange: (catalog: CatalogDraft) => void;
  onFileChange: (file: File | null) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const [fileError, setFileError] = useState("");

  const updateCatalog = (patch: Partial<CatalogDraft>) => {
    onChange({ ...catalog, ...patch });
  };

  const selectMethod = (method: CatalogImportMethod) => {
    setFileError("");
    onFileChange(null);
    updateCatalog({
      method,
      confirmed: method === "skip",
      sourceName: method === "skip" ? "Skipped during onboarding" : "",
      storeProvider: null,
      pastedText: method === "paste" ? catalog.pastedText : "",
      products:
        method === "manual"
          ? catalog.products.length === 0
            ? [createBlankCatalogProduct()]
            : catalog.products
          : [],
    });
  };

  const updateProduct = (
    id: string,
    field: keyof CatalogDraftProduct,
    value: string,
  ) => {
    updateCatalog({
      products: catalog.products.map((product) =>
        product.id === id ? { ...product, [field]: value } : product,
      ),
    });
  };

  const handleFile = (file?: File) => {
    if (!file) return;
    const error = catalogFileValidationMessage(file);
    if (error) {
      setFileError(error);
      onFileChange(null);
      updateCatalog({ confirmed: false, sourceName: "" });
      return;
    }
    setFileError("");
    onFileChange(file);
    updateCatalog({
      confirmed: true,
      sourceName: file.name,
      products: [],
    });
  };

  const handleFileInput = (event: ChangeEvent<HTMLInputElement>) => {
    handleFile(event.target.files?.[0]);
    event.target.value = "";
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    handleFile(event.dataTransfer.files?.[0]);
  };

  const preparePaste = () => {
    try {
      const file = createPasteCatalogFile(
        catalog.pastedText,
        catalog.defaultItemType,
      );
      setFileError("");
      onFileChange(file);
      updateCatalog({
        confirmed: true,
        sourceName: "Pasted catalog list",
      });
    } catch (error) {
      setFileError(
        error instanceof Error ? error.message : "Check the pasted list.",
      );
      onFileChange(null);
      updateCatalog({ confirmed: false });
    }
  };

  const pastedLines = pasteListLines(catalog.pastedText);

  return (
    <div className="onboarding-panel catalog-onboarding-panel">
      <div className="onboarding-list-head">
        <div>
          <div className="eyebrow">Your offer</div>
          <h2>Products and services</h2>
        </div>
        {catalog.method === "manual" && (
          <Button
            variant="soft"
            className="btn-sm"
            onClick={() =>
              updateCatalog({
                products: [...catalog.products, createBlankCatalogProduct()],
              })
            }
            data-testid="button-add-onboarding-product"
          >
            <Plus /> Add item
          </Button>
        )}
      </div>

      <div className="catalog-import-options">
        {importOptions.map(({ id, title, copy, icon: Icon }) => (
          <button
            key={id}
            className={cx(
              "catalog-import-option",
              catalog.method === id && "active",
            )}
            onClick={() => selectMethod(id)}
            data-testid={`button-catalog-method-${id}`}
          >
            <div className="integration-icon">
              <Icon />
            </div>
            <strong>{title}</strong>
            <span>{copy}</span>
            {catalog.method === id && (
              <i className="catalog-option-check">
                <Check />
              </i>
            )}
          </button>
        ))}
      </div>

      {!catalog.method && (
        <div className="onboarding-tip catalog-choice-tip">
          <Lightbulb size={16} />
          <span>
            Bulk setup is the fastest path. Your catalog is saved only after the
            business exists and the real API validates it.
          </span>
        </div>
      )}

      {catalog.method === "manual" && (
        <div className="catalog-method-panel">
          <div className="catalog-method-heading">
            <div>
              <strong>Add a small catalog manually</strong>
              <span>
                These drafts will use the same atomic import API during setup.
              </span>
            </div>
          </div>
          <div className="onboarding-products">
            {catalog.products.map((product, index) => (
              <div className="onboarding-product-row" key={product.id}>
                <div className="product-number">
                  {String(index + 1).padStart(2, "0")}
                </div>
                <div className="field">
                  <label>Type</label>
                  <select
                    value={product.itemType}
                    onChange={(event) =>
                      updateProduct(product.id, "itemType", event.target.value)
                    }
                  >
                    <option value="product">Product</option>
                    <option value="service">Service</option>
                  </select>
                </div>
                <div className="field catalog-draft-name">
                  <label>Name</label>
                  <input
                    value={product.name}
                    onChange={(event) =>
                      updateProduct(product.id, "name", event.target.value)
                    }
                    placeholder="Product or service"
                    data-testid={`input-onboarding-product-name-${index}`}
                  />
                </div>
                <div className="field">
                  <label>Price</label>
                  <input
                    inputMode="decimal"
                    value={product.price}
                    onChange={(event) =>
                      updateProduct(product.id, "price", event.target.value)
                    }
                    placeholder="Optional"
                    data-testid={`input-onboarding-product-price-${index}`}
                  />
                </div>
                <div className="field">
                  <label>SKU</label>
                  <input
                    value={product.sku}
                    onChange={(event) =>
                      updateProduct(product.id, "sku", event.target.value)
                    }
                    placeholder="Optional"
                  />
                </div>
                <div className="field catalog-draft-description">
                  <label>Description</label>
                  <input
                    value={product.description}
                    onChange={(event) =>
                      updateProduct(
                        product.id,
                        "description",
                        event.target.value,
                      )
                    }
                    placeholder="Optional"
                  />
                </div>
                {catalog.products.length > 1 && (
                  <button
                    className="icon-btn catalog-draft-remove"
                    onClick={() =>
                      updateCatalog({
                        products: catalog.products.filter(
                          (item) => item.id !== product.id,
                        ),
                      })
                    }
                    aria-label={`Remove item ${index + 1}`}
                  >
                    <X size={14} />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {catalog.method === "upload" && (
        <div className="catalog-method-panel">
          <div className="catalog-method-heading">
            <div>
              <strong>Upload CSV or Excel</strong>
              <span>
                The server will detect columns and validate every row after the
                business is created.
              </span>
            </div>
            <Badge>Real import</Badge>
          </div>
          <div
            className={cx("catalog-upload-zone", dragging && "dragging")}
            onDragEnter={() => setDragging(true)}
            onDragLeave={() => setDragging(false)}
            onDragOver={(event) => event.preventDefault()}
            onDrop={handleDrop}
          >
            <UploadCloud />
            <strong>Drop a .csv or .xlsx file here</strong>
            <span>Maximum 10 MB · legacy .xls is not supported</span>
            <label className="btn btn-soft btn-sm">
              Choose file
              <input
                type="file"
                accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                onChange={handleFileInput}
                data-testid="input-catalog-file"
              />
            </label>
            {selectedFile && (
              <small>
                <FileSpreadsheet /> {selectedFile.name}
              </small>
            )}
          </div>
          <div className="catalog-expected-columns">
            <span>Automatic aliases</span>
            <div>
              {expectedCatalogColumns.map((column) => (
                <Badge key={column}>{column}</Badge>
              ))}
            </div>
          </div>
          {catalog.confirmed && selectedFile && (
            <div className="onboarding-tip catalog-confirmed">
              <Check size={16} />
              <span>
                File kept for this browser session. It will be previewed, then
                imported atomically after the business is saved.
              </span>
            </div>
          )}
        </div>
      )}

      {catalog.method === "paste" && (
        <div className="catalog-method-panel">
          <div className="catalog-method-heading">
            <div>
              <strong>Paste one item per line</strong>
              <span>
                No AI parsing or price extraction—just reliable names.
              </span>
            </div>
            <Badge>{pastedLines.length} items</Badge>
          </div>
          <div
            className="catalog-type-choice"
            role="group"
            aria-label="Pasted item type"
          >
            {(["product", "service"] as CatalogItemType[]).map((itemType) => (
              <button
                key={itemType}
                className={cx(catalog.defaultItemType === itemType && "active")}
                onClick={() => {
                  onFileChange(null);
                  updateCatalog({
                    defaultItemType: itemType,
                    confirmed: false,
                  });
                }}
              >
                {itemType === "product" ? "Products" : "Services"}
              </button>
            ))}
          </div>
          <div className="field">
            <label>Item names</label>
            <textarea
              className="catalog-paste-area"
              value={catalog.pastedText}
              onChange={(event) => {
                onFileChange(null);
                updateCatalog({
                  pastedText: event.target.value,
                  confirmed: false,
                  sourceName: "",
                });
              }}
              placeholder={"Apples\nBananas\nOrange Juice\nMilk"}
              data-testid="textarea-paste-catalog"
            />
          </div>
          {pastedLines.length > 0 && (
            <div className="catalog-name-preview">
              {pastedLines.slice(0, 8).map((line, index) => (
                <span key={`${line}-${index}`}>{line}</span>
              ))}
              {pastedLines.length > 8 && (
                <span>+{pastedLines.length - 8} more</span>
              )}
            </div>
          )}
          <div className="catalog-confirm-row">
            <div>
              <strong>Up to 2,000 names</strong>
              <span>Blank lines are ignored and names are quoted safely.</span>
            </div>
            <Button
              variant="green"
              className="btn-sm"
              onClick={preparePaste}
              data-testid="button-confirm-paste-import"
            >
              <Check /> Prepare list
            </Button>
          </div>
          {catalog.confirmed && selectedFile && (
            <div className="onboarding-tip catalog-confirmed">
              <Check size={16} />
              <span>
                The generated CSV will use the real preview and atomic import
                endpoints after the business is saved.
              </span>
            </div>
          )}
        </div>
      )}

      {catalog.method === "store" && (
        <div className="catalog-method-panel catalog-skip-panel">
          <div className="success-mark catalog-skip-mark">
            <Plug />
          </div>
          <div>
            <strong>Store provider configuration required</strong>
            <p>
              Shopify and WooCommerce catalog connectors are not configured on
              this platform. Choose upload, paste, manual entry, or skip this step.
            </p>
          </div>
        </div>
      )}

      {catalog.method === "skip" && (
        <div className="catalog-method-panel catalog-skip-panel">
          <div className="success-mark catalog-skip-mark">
            <Clock3 />
          </div>
          <div>
            <strong>Continue without a catalog</strong>
            <p>
              Your business will be created with an empty catalog. Upload,
              paste, or add items later from Products &amp; Services.
            </p>
          </div>
        </div>
      )}

      {fileError && (
        <div className="catalog-inline-error" role="alert">
          <AlertCircle /> {fileError}
        </div>
      )}
    </div>
  );
}
