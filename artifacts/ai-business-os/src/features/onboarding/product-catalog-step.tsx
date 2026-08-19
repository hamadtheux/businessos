import {
  useState,
  type ChangeEvent,
  type DragEvent,
  type ReactNode,
} from "react";
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
  Store,
  UploadCloud,
  X,
} from "lucide-react";
import { Badge, Button } from "@/components/product-ui";
import { cx } from "@/lib/product-utils";
import {
  createBlankCatalogProduct,
  createWorkbookPrototypeRows,
  expectedCatalogColumns,
  parseCatalogRows,
  validCatalogProducts,
  type CatalogDraft,
  type CatalogDraftProduct,
  type CatalogImportMethod,
  type CatalogPreviewRow,
  type CatalogStoreProvider,
} from "./catalog-import";

const importOptions: Array<{
  id: CatalogImportMethod;
  title: string;
  copy: string;
  icon: typeof ListPlus;
}> = [
  {
    id: "manual",
    title: "Add manually",
    copy: "Best for a small catalog",
    icon: ListPlus,
  },
  {
    id: "upload",
    title: "Upload CSV / Excel",
    copy: "Preview and confirm a file",
    icon: FileSpreadsheet,
  },
  {
    id: "store",
    title: "Import from store",
    copy: "Prepare a future store sync",
    icon: ShoppingBag,
  },
  {
    id: "paste",
    title: "Paste product list",
    copy: "Paste rows from a spreadsheet",
    icon: Package,
  },
  {
    id: "skip",
    title: "Skip for now",
    copy: "Add your catalog later",
    icon: Clock3,
  },
];

const storeOptions: Array<{
  name: CatalogStoreProvider;
  copy: string;
  icon: typeof ShoppingBag;
}> = [
  {
    name: "Shopify",
    copy: "Products, variants, prices, and inventory",
    icon: ShoppingBag,
  },
  {
    name: "WooCommerce",
    copy: "Catalog and stock from your WordPress store",
    icon: Store,
  },
  {
    name: "Custom Store / API",
    copy: "Prepare a backend-managed catalog connection",
    icon: Plug,
  },
];

function CatalogPreview({
  rows,
  action,
}: {
  rows: CatalogPreviewRow[];
  action: ReactNode;
}) {
  const validRows = rows.filter((row) => row.errors.length === 0).length;
  const errorRows = rows.length - validRows;

  return (
    <div className="catalog-preview">
      <div className="catalog-preview-summary">
        <div>
          <span>Total rows</span>
          <strong>{rows.length}</strong>
        </div>
        <div>
          <span>Valid rows</span>
          <strong>{validRows}</strong>
        </div>
        <div>
          <span>Rows with errors</span>
          <strong>{errorRows}</strong>
        </div>
        {action}
      </div>
      <div className="table-scroll catalog-preview-table">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>SKU</th>
              <th>Price</th>
              <th>Stock / availability</th>
              <th>Category</th>
              <th>Description</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 8).map((row) => (
              <tr key={row.id}>
                <td>
                  <strong>{row.name || "—"}</strong>
                </td>
                <td>{row.sku || "—"}</td>
                <td>{row.price ? `$${row.price}` : "—"}</td>
                <td>{row.availability}</td>
                <td>{row.category || "—"}</td>
                <td>{row.description || "—"}</td>
                <td>
                  <Badge tone={row.errors.length ? "danger" : "success"}>
                    {row.errors.length ? row.errors.join(", ") : "Valid"}
                  </Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > 8 && (
        <div className="catalog-preview-more">
          Showing 8 of {rows.length} rows
        </div>
      )}
    </div>
  );
}

export function ProductCatalogStep({
  catalog,
  onChange,
}: {
  catalog: CatalogDraft;
  onChange: (catalog: CatalogDraft) => void;
}) {
  const [previewRows, setPreviewRows] = useState<CatalogPreviewRow[]>(() =>
    catalog.confirmed
      ? catalog.products.map((product) => ({ ...product, errors: [] }))
      : catalog.method === "paste"
        ? parseCatalogRows(catalog.pastedText)
        : [],
  );
  const [dragging, setDragging] = useState(false);
  const [fileError, setFileError] = useState("");

  const updateCatalog = (patch: Partial<CatalogDraft>) => {
    onChange({ ...catalog, ...patch });
  };

  const selectMethod = (method: CatalogImportMethod) => {
    setPreviewRows([]);
    setFileError("");
    updateCatalog({
      method,
      confirmed: method === "skip",
      sourceName: method === "skip" ? "Skipped during onboarding" : "",
      storeProvider: null,
      products:
        method === "skip"
          ? []
          : method === "manual" && catalog.products.length === 0
            ? [createBlankCatalogProduct()]
            : catalog.products,
    });
  };

  const updateProduct = (
    id: string,
    field: keyof CatalogDraftProduct,
    value: string,
  ) => {
    updateCatalog({
      confirmed: false,
      products: catalog.products.map((product) =>
        product.id === id ? { ...product, [field]: value } : product,
      ),
    });
  };

  const addProduct = () => {
    updateCatalog({
      products: [...catalog.products, createBlankCatalogProduct()],
    });
  };

  const handleFile = async (file?: File) => {
    if (!file) return;
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (extension !== "csv" && extension !== "xlsx") {
      setFileError("Choose a .csv or .xlsx file to continue.");
      setPreviewRows([]);
      updateCatalog({ confirmed: false, sourceName: "" });
      return;
    }

    const rows =
      extension === "csv"
        ? parseCatalogRows(await file.text())
        : createWorkbookPrototypeRows();
    setFileError(rows.length ? "" : "No product rows were found in this file.");
    setPreviewRows(rows);
    updateCatalog({
      confirmed: false,
      sourceName: file.name,
      products: [],
    });
  };

  const handleFileInput = (event: ChangeEvent<HTMLInputElement>) => {
    void handleFile(event.target.files?.[0]);
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    void handleFile(event.dataTransfer.files?.[0]);
  };

  const confirmRows = (sourceName: string) => {
    const products = validCatalogProducts(previewRows);
    if (!products.length) return;
    updateCatalog({ confirmed: true, sourceName, products });
  };

  const updatePastedText = (value: string) => {
    const rows = parseCatalogRows(value);
    setPreviewRows(rows);
    updateCatalog({
      pastedText: value,
      confirmed: false,
      sourceName: "Pasted product list",
      products: [],
    });
  };

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
            onClick={addProduct}
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
            Choose the fastest way to add your catalog. Nothing is imported
            until you review and confirm it.
          </span>
        </div>
      )}

      {catalog.method === "manual" && (
        <div className="catalog-method-panel">
          <div className="catalog-method-heading">
            <div>
              <strong>Add products manually</strong>
              <span>Good for small catalogs and service lists.</span>
            </div>
          </div>
          <div className="onboarding-products">
            {catalog.products.map((product, index) => (
              <div className="onboarding-product-row" key={product.id}>
                <div className="product-number">
                  {String(index + 1).padStart(2, "0")}
                </div>
                <div className="field">
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
                    type="number"
                    min="0"
                    value={product.price}
                    onChange={(event) =>
                      updateProduct(product.id, "price", event.target.value)
                    }
                    placeholder="0"
                    data-testid={`input-onboarding-product-price-${index}`}
                  />
                </div>
                <div className="field">
                  <label>Stock / availability</label>
                  <input
                    value={product.availability}
                    onChange={(event) =>
                      updateProduct(
                        product.id,
                        "availability",
                        event.target.value,
                      )
                    }
                    placeholder="In stock"
                    data-testid={`input-onboarding-product-availability-${index}`}
                  />
                </div>
                {catalog.products.length > 1 && (
                  <button
                    className="icon-btn"
                    onClick={() =>
                      updateCatalog({
                        products: catalog.products.filter(
                          (item) => item.id !== product.id,
                        ),
                      })
                    }
                    aria-label={`Remove product ${index + 1}`}
                  >
                    <X size={14} />
                  </button>
                )}
              </div>
            ))}
          </div>
          <div className="onboarding-tip">
            <Lightbulb size={16} />
            <span>
              You can update this list any time from Settings. Your AI team uses
              availability when answering customer questions.
            </span>
          </div>
        </div>
      )}

      {catalog.method === "upload" && (
        <div className="catalog-method-panel">
          <div className="catalog-method-heading">
            <div>
              <strong>Upload CSV or Excel</strong>
              <span>Review every row before adding it to your workspace.</span>
            </div>
            <Badge>Prototype import</Badge>
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
            <span>or choose a file from your computer</span>
            <label className="btn btn-soft btn-sm">
              Choose file
              <input
                type="file"
                accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                onChange={handleFileInput}
                data-testid="input-catalog-file"
              />
            </label>
            {catalog.sourceName && (
              <small>
                <FileSpreadsheet /> {catalog.sourceName}
              </small>
            )}
          </div>
          <div className="catalog-expected-columns">
            <span>Expected columns</span>
            <div>
              {expectedCatalogColumns.map((column) => (
                <Badge key={column}>{column}</Badge>
              ))}
            </div>
          </div>
          {fileError && (
            <div className="catalog-inline-error">
              <AlertCircle /> {fileError}
            </div>
          )}
          {previewRows.length > 0 && (
            <CatalogPreview
              rows={previewRows}
              action={
                <Button
                  variant="green"
                  className="btn-sm"
                  disabled={!previewRows.some((row) => !row.errors.length)}
                  onClick={() =>
                    confirmRows(catalog.sourceName || "Uploaded catalog")
                  }
                  data-testid="button-confirm-file-import"
                >
                  <Check /> Confirm import
                </Button>
              }
            />
          )}
          {catalog.confirmed && (
            <div className="onboarding-tip catalog-confirmed">
              <Check size={16} />
              <span>
                {catalog.products.length} valid products are ready to import
                into this business workspace.
              </span>
            </div>
          )}
          <div className="prototype-note catalog-prototype-note">
            The browser only prepares this prototype preview. File storage and
            durable parsing will be handled by the future backend.
          </div>
        </div>
      )}

      {catalog.method === "store" && (
        <div className="catalog-method-panel">
          <div className="catalog-method-heading">
            <div>
              <strong>Import from your store</strong>
              <span>Choose the connection your backend will sync later.</span>
            </div>
            <Badge>Frontend prototype</Badge>
          </div>
          <div className="catalog-store-grid">
            {storeOptions.map(({ name, copy, icon: Icon }) => (
              <button
                key={name}
                className={cx(
                  "onboarding-channel-card catalog-store-card",
                  catalog.storeProvider === name && "connected",
                )}
                onClick={() =>
                  updateCatalog({
                    storeProvider: name,
                    sourceName: name,
                    confirmed: false,
                    products: [],
                  })
                }
                data-testid={`button-store-${name.toLowerCase().replaceAll(" ", "-")}`}
              >
                <div className="integration-icon">
                  <Icon />
                </div>
                <div className="row-main">
                  <div className="row-title">{name}</div>
                  <div className="row-copy">{copy}</div>
                </div>
                {catalog.storeProvider === name && <Check />}
              </button>
            ))}
          </div>
          <div className="onboarding-tip">
            <Plug size={16} />
            <span>
              No secret credentials are requested here. Authentication and real
              catalog sync will be connected securely through the future
              backend.
            </span>
          </div>
          {catalog.storeProvider && (
            <div className="catalog-confirm-row">
              <div>
                <strong>{catalog.storeProvider} selected</strong>
                <span>
                  Save this prototype connection for the new workspace.
                </span>
              </div>
              <Button
                variant="green"
                className="btn-sm"
                onClick={() => updateCatalog({ confirmed: true })}
                data-testid="button-confirm-store-import"
              >
                <Check /> Confirm connection
              </Button>
            </div>
          )}
          {catalog.confirmed && (
            <div className="onboarding-tip catalog-confirmed">
              <Check size={16} />
              <span>
                Prototype connection confirmed. No external account was
                accessed.
              </span>
            </div>
          )}
        </div>
      )}

      {catalog.method === "paste" && (
        <div className="catalog-method-panel">
          <div className="catalog-method-heading">
            <div>
              <strong>Paste a product list</strong>
              <span>Copy rows directly from Excel, Sheets, or a CSV.</span>
            </div>
          </div>
          <div className="field">
            <label>Product rows</label>
            <textarea
              className="catalog-paste-area"
              value={catalog.pastedText}
              onChange={(event) => updatePastedText(event.target.value)}
              placeholder={expectedCatalogColumns.join("\t")}
              data-testid="textarea-paste-catalog"
            />
          </div>
          <div className="catalog-expected-columns">
            <span>Column order</span>
            <div>
              {expectedCatalogColumns.map((column) => (
                <Badge key={column}>{column}</Badge>
              ))}
            </div>
          </div>
          {previewRows.length > 0 && (
            <CatalogPreview
              rows={previewRows}
              action={
                <Button
                  variant="green"
                  className="btn-sm"
                  disabled={!previewRows.some((row) => !row.errors.length)}
                  onClick={() => confirmRows("Pasted product list")}
                  data-testid="button-confirm-paste-import"
                >
                  <Check /> Confirm import
                </Button>
              }
            />
          )}
          {catalog.confirmed && (
            <div className="onboarding-tip catalog-confirmed">
              <Check size={16} />
              <span>
                {catalog.products.length} valid products are ready to import
                into this business workspace.
              </span>
            </div>
          )}
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
              You can add or sync your catalog later from Settings or
              Integrations.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
