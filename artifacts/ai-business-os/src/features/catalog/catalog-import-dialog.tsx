import { useState, type ChangeEvent, type DragEvent } from "react";
import {
  AlertCircle,
  Check,
  FileSpreadsheet,
  RefreshCw,
  UploadCloud,
} from "lucide-react";
import { Badge, Button, Modal } from "@/components/product-ui";
import { cx } from "@/lib/product-utils";
import {
  catalogApi,
  catalogImportPreviewFromError,
  humanizeCatalogError,
} from "@/services/catalog";
import type {
  CatalogImportField,
  CatalogImportPreviewResponse,
  CatalogItemType,
} from "@/services/api-types";
import {
  canImportCatalogPreview,
  catalogFileValidationMessage,
  createPasteCatalogFile,
  formatCatalogPrice,
  pasteListLines,
} from "./catalog-model";

const fieldLabels: Record<CatalogImportField, string> = {
  name: "Name",
  item_type: "Type",
  description: "Description",
  sku: "SKU",
  price: "Price",
  status: "Status",
};

export function CatalogImportDialog({
  businessId,
  currency,
  locale,
  mode,
  onClose,
  onImported,
}: {
  businessId: string;
  currency: string;
  locale: string;
  mode: "upload" | "paste";
  onClose: () => void;
  onImported: (createdCount: number) => void;
}) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<CatalogImportPreviewResponse | null>(
    null,
  );
  const [pastedText, setPastedText] = useState("");
  const [defaultItemType, setDefaultItemType] =
    useState<CatalogItemType>("product");
  const [dragging, setDragging] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");

  const previewFile = async (file: File) => {
    const validationError = catalogFileValidationMessage(file);
    if (validationError) {
      setSelectedFile(null);
      setPreview(null);
      setError(validationError);
      return;
    }
    setSelectedFile(file);
    setPreview(null);
    setPreviewing(true);
    setError("");
    try {
      setPreview(await catalogApi.previewCatalogImport(businessId, file));
    } catch (reason) {
      setError(
        humanizeCatalogError(
          reason,
          "We couldn't preview this file. Keep it selected and try again.",
        ),
      );
    } finally {
      setPreviewing(false);
    }
  };

  const chooseFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) void previewFile(file);
    event.target.value = "";
  };

  const dropFile = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file) void previewFile(file);
  };

  const previewPaste = () => {
    try {
      void previewFile(createPasteCatalogFile(pastedText, defaultItemType));
    } catch (reason) {
      setSelectedFile(null);
      setPreview(null);
      setError(
        reason instanceof Error ? reason.message : "Check the pasted list.",
      );
    }
  };

  const commitImport = async () => {
    if (!selectedFile || !canImportCatalogPreview(preview)) return;
    setImporting(true);
    setError("");
    try {
      const result = await catalogApi.importCatalogFile(
        businessId,
        selectedFile,
      );
      onImported(result.created_count);
    } catch (reason) {
      const validationPreview = catalogImportPreviewFromError(reason);
      if (validationPreview) setPreview(validationPreview);
      setError(
        humanizeCatalogError(
          reason,
          "We couldn't import this catalog. Your file is still selected—please try again.",
        ),
      );
    } finally {
      setImporting(false);
    }
  };

  const lines = pasteListLines(pastedText);

  return (
    <Modal
      wide
      title={mode === "upload" ? "Upload CSV or Excel" : "Paste a list"}
      description="Preview first, then import every valid row atomically."
      onClose={onClose}
    >
      {mode === "upload" ? (
        <div
          className={cx("catalog-upload-zone", dragging && "dragging")}
          onDragEnter={() => setDragging(true)}
          onDragLeave={() => setDragging(false)}
          onDragOver={(event) => event.preventDefault()}
          onDrop={dropFile}
        >
          <UploadCloud />
          <strong>Drop a .csv or .xlsx file here</strong>
          <span>Maximum 10 MB · .xls is not supported</span>
          <label className="btn btn-soft btn-sm">
            Choose file
            <input
              type="file"
              accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={chooseFile}
              data-testid="input-workspace-catalog-file"
            />
          </label>
          {selectedFile && (
            <small>
              <FileSpreadsheet /> {selectedFile.name}
            </small>
          )}
        </div>
      ) : (
        <div className="catalog-paste-workflow">
          <div
            className="catalog-type-choice"
            role="group"
            aria-label="Pasted item type"
          >
            {(["product", "service"] as CatalogItemType[]).map((itemType) => (
              <button
                key={itemType}
                type="button"
                className={defaultItemType === itemType ? "active" : ""}
                onClick={() => {
                  setDefaultItemType(itemType);
                  setSelectedFile(null);
                  setPreview(null);
                }}
              >
                {itemType === "product" ? "Products" : "Services"}
              </button>
            ))}
          </div>
          <div className="field">
            <label>One item name per line</label>
            <textarea
              className="catalog-paste-area"
              value={pastedText}
              onChange={(event) => {
                setPastedText(event.target.value);
                setSelectedFile(null);
                setPreview(null);
              }}
              placeholder={"Apples\nBananas\nOrange Juice\nMilk"}
              data-testid="textarea-workspace-catalog-paste"
            />
          </div>
          <div className="catalog-confirm-row">
            <div>
              <strong>{lines.length} item names ready</strong>
              <span>Blank lines are ignored · maximum 2,000</span>
            </div>
            <Button
              variant="green"
              className="btn-sm"
              onClick={previewPaste}
              disabled={previewing || lines.length === 0}
              data-testid="button-preview-pasted-catalog"
            >
              {previewing ? (
                <RefreshCw className="spin" />
              ) : (
                <FileSpreadsheet />
              )}
              Preview list
            </Button>
          </div>
        </div>
      )}

      {previewing && (
        <div className="catalog-preview-loading">
          <RefreshCw className="spin" /> Detecting columns and validating rows…
        </div>
      )}

      {preview && (
        <CatalogServerPreview
          preview={preview}
          currency={currency}
          locale={locale}
        />
      )}

      {error && (
        <div className="catalog-inline-error" role="alert">
          <AlertCircle /> {error}
        </div>
      )}

      {preview?.invalid_rows ? (
        <div className="catalog-atomic-note">
          Fix the highlighted rows and upload the file again. Nothing has been
          imported yet.
        </div>
      ) : null}

      <div className="modal-foot">
        <Button type="button" onClick={onClose} disabled={importing}>
          Cancel
        </Button>
        {selectedFile && !preview && !previewing && (
          <Button
            type="button"
            variant="soft"
            onClick={() => void previewFile(selectedFile)}
          >
            <RefreshCw /> Retry preview
          </Button>
        )}
        <Button
          type="button"
          variant="primary"
          onClick={() => void commitImport()}
          disabled={!canImportCatalogPreview(preview) || importing}
          data-testid="button-commit-catalog-import"
        >
          {importing ? (
            <>
              <RefreshCw className="spin" /> Importing…
            </>
          ) : (
            <>
              <Check /> Import {preview?.valid_rows ?? 0} items
            </>
          )}
        </Button>
      </div>
    </Modal>
  );
}

function CatalogServerPreview({
  preview,
  currency,
  locale,
}: {
  preview: CatalogImportPreviewResponse;
  currency: string;
  locale: string;
}) {
  return (
    <div className="catalog-preview catalog-server-preview">
      <div className="catalog-preview-summary">
        <div>
          <span>File</span>
          <strong>{preview.file.filename}</strong>
        </div>
        <div>
          <span>Total rows</span>
          <strong>{preview.total_rows}</strong>
        </div>
        <div>
          <span>Valid</span>
          <strong>{preview.valid_rows}</strong>
        </div>
        <div>
          <span>Invalid</span>
          <strong>{preview.invalid_rows}</strong>
        </div>
      </div>
      <div className="catalog-detected-columns">
        <div>
          <Check />
          <strong>We matched your columns automatically.</strong>
        </div>
        <div className="catalog-mapping-list">
          {Object.entries(preview.detected_columns).map(([field, source]) => (
            <Badge key={field}>
              {source} → {fieldLabels[field as CatalogImportField] ?? field}
            </Badge>
          ))}
        </div>
      </div>
      <div className="table-scroll catalog-preview-table">
        <table>
          <thead>
            <tr>
              <th>Row</th>
              <th>Name</th>
              <th>Type</th>
              <th>SKU</th>
              <th>Price</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {preview.preview_rows.slice(0, 8).map((row) => (
              <tr key={row.row}>
                <td>{row.row}</td>
                <td>
                  <strong>{row.normalized.name || "—"}</strong>
                </td>
                <td>{row.normalized.item_type || "—"}</td>
                <td>{row.normalized.sku || "—"}</td>
                <td>
                  {row.item
                    ? formatCatalogPrice(
                        row.item.price ?? null,
                        currency,
                        locale,
                      )
                    : row.normalized.price || "—"}
                </td>
                <td>
                  <Badge tone={row.errors.length ? "danger" : "success"}>
                    {row.errors.length ? "Needs attention" : "Valid"}
                  </Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {preview.errors.length > 0 && (
        <div className="catalog-error-list">
          {preview.errors.map((rowError, index) => (
            <div key={`${rowError.row}-${rowError.field ?? "row"}-${index}`}>
              <AlertCircle />
              <span>
                <strong>Row {rowError.row}</strong>
                {rowError.message}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
