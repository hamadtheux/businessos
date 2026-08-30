import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from "react";
import {
  BarChart3,
  Bot,
  Check,
  CloudUpload,
  LayoutDashboard,
  Palette,
  Sparkles,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/product-ui";
import {
  BRAND_PRESETS,
  brandThemeStyle,
  deriveBrandTheme,
  getDefaultBrandIdentity,
  isValidHex,
  normalizeHex,
  resolveBrandIdentity,
  type BrandIdentityDraft,
} from "@/lib/brand-theme";
import { cx, initials } from "@/lib/product-utils";
import {
  BRAND_LOGO_ACCEPT,
  readBrandLogo,
  revokeBrandLogo,
} from "@/services/brand-logo";
import {
  BRAND_LOGO_MAX_BYTES,
  BRAND_LOGO_MAX_MEGABYTES,
  type BrandIdentity,
} from "@/types/business";

function logoSource(identity: Pick<BrandIdentity, "logo" | "logoUrl">) {
  if (
    identity.logo &&
    identity.logo.size > 0 &&
    identity.logo.size <= BRAND_LOGO_MAX_BYTES &&
    ["image/png", "image/jpeg", "image/webp"].includes(
      identity.logo.mimeType,
    ) &&
    identity.logo.previewUrl.startsWith("blob:")
  ) {
    return identity.logo.previewUrl;
  }
  if (identity.logoUrl && /^(https?:\/\/|\/)/.test(identity.logoUrl)) {
    return identity.logoUrl;
  }
  return undefined;
}

export function BusinessBrandMark({
  businessName,
  identity,
  className,
}: {
  businessName: string;
  identity?: Pick<BrandIdentity, "logo" | "logoUrl">;
  className?: string;
}) {
  const source = identity ? logoSource(identity) : undefined;
  const [logoAspectRatio, setLogoAspectRatio] = useState(1);

  useEffect(() => setLogoAspectRatio(1), [source]);

  return (
    <div
      className={cx("business-brand-mark", source && "has-logo", className)}
      style={source ? { aspectRatio: logoAspectRatio } : undefined}
    >
      {source ? (
        <img
          src={source}
          alt={`${businessName} logo`}
          onLoad={(event) => {
            const { naturalHeight, naturalWidth } = event.currentTarget;
            if (naturalHeight && naturalWidth) {
              setLogoAspectRatio(naturalWidth / naturalHeight);
            }
          }}
        />
      ) : (
        <span>{initials(businessName || "Business")}</span>
      )}
    </div>
  );
}

function ColorField({
  id,
  label,
  value,
  fallback,
  required = false,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  fallback: string;
  required?: boolean;
  onChange: (value: string) => void;
}) {
  const normalized = normalizeHex(value);
  const invalid = Boolean(value) && !normalized;
  return (
    <div className="brand-color-field">
      <label htmlFor={`${id}-hex`}>
        {label}
        {!required && <span>Optional</span>}
      </label>
      <div className={cx("brand-color-control", invalid && "invalid")}>
        <input
          id={`${id}-picker`}
          className="brand-color-picker"
          type="color"
          value={normalized ?? fallback}
          onChange={(event) => onChange(event.target.value.toUpperCase())}
          aria-label={`Choose ${label.toLowerCase()}`}
        />
        <span aria-hidden="true">#</span>
        <input
          id={`${id}-hex`}
          className="brand-hex-input"
          value={value.replace(/^#/, "")}
          onChange={(event) => onChange(`#${event.target.value}`)}
          onBlur={() => normalized && onChange(normalized)}
          placeholder={fallback.slice(1)}
          maxLength={6}
          spellCheck={false}
          aria-invalid={invalid}
          aria-describedby={`${id}-help`}
        />
      </div>
      <small id={`${id}-help`} className={invalid ? "brand-field-error" : ""}>
        {invalid
          ? "Use a 3 or 6 digit HEX value."
          : !value && !required
            ? `Auto-derived in preview: ${fallback}`
            : required
              ? "Used for primary actions and selected states."
              : "Custom support color."}
      </small>
    </div>
  );
}

function LogoUploader({
  businessName,
  value,
  onChange,
}: {
  businessName: string;
  value: BrandIdentityDraft;
  onChange: (next: BrandIdentityDraft) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");
  const source = logoSource(value);

  const chooseFile = (file?: File) => {
    if (!file) return;
    setError("");
    try {
      const logo = readBrandLogo(file);
      revokeBrandLogo(value.logo);
      onChange({ ...value, logo });
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Choose another image.",
      );
    } finally {
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const drop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    setDragging(false);
    chooseFile(event.dataTransfer.files[0]);
  };

  const inputChanged = (event: ChangeEvent<HTMLInputElement>) => {
    chooseFile(event.target.files?.[0]);
  };

  return (
    <div className="brand-logo-field">
      <div className="brand-setting-label">
        Business logo <span>Optional</span>
      </div>
      {source ? (
        <div className="brand-logo-selected">
          <div className="brand-logo-neutral-surface">
            <BusinessBrandMark businessName={businessName} identity={value} />
          </div>
          <div className="brand-logo-file-copy">
            <strong>{value.logo?.name ?? "Current business logo"}</strong>
            <span>Displayed without cropping or distortion</span>
          </div>
          <div className="brand-logo-actions">
            <label
              className="btn btn-secondary btn-sm"
              htmlFor="brand-logo-file"
            >
              Change
            </label>
            <Button
              variant="danger"
              className="btn-sm"
              type="button"
              aria-label="Remove business logo"
              onClick={() => {
                revokeBrandLogo(value.logo);
                onChange({ ...value, logo: undefined, logoUrl: undefined });
              }}
            >
              <Trash2 /> Remove
            </Button>
          </div>
        </div>
      ) : (
        <label
          className={cx("brand-logo-dropzone", dragging && "dragging")}
          htmlFor="brand-logo-file"
          onDragEnter={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={() => setDragging(false)}
          onDrop={drop}
        >
          <CloudUpload />
          <strong>Drop your logo here, or choose a file</strong>
          <span>
            PNG, JPG, JPEG, or WebP · up to {BRAND_LOGO_MAX_MEGABYTES} MB
          </span>
          <span className="btn btn-secondary btn-sm">Choose logo</span>
        </label>
      )}
      <input
        ref={inputRef}
        id="brand-logo-file"
        className="visually-hidden"
        type="file"
        accept={BRAND_LOGO_ACCEPT}
        onChange={inputChanged}
      />
      {error && (
        <div className="brand-logo-error" role="alert">
          {error}
        </div>
      )}
    </div>
  );
}

export function BrandThemePreview({
  businessName,
  value,
  legacyTheme = "green",
}: {
  businessName: string;
  value: BrandIdentityDraft;
  legacyTheme?: "green" | "navy";
}) {
  const fallback = getDefaultBrandIdentity(legacyTheme);
  const previewIdentity: BrandIdentity = {
    logo: value.logo,
    logoUrl: value.logoUrl,
    primaryColor: normalizeHex(value.primaryColor) ?? fallback.primaryColor,
    secondaryColor: normalizeHex(value.secondaryColor) ?? undefined,
    accentColor: normalizeHex(value.accentColor) ?? undefined,
  };
  const tokens = deriveBrandTheme(previewIdentity, legacyTheme);

  return (
    <div className="brand-preview-wrap">
      <div className="brand-preview-heading">
        <div>
          <span>Live workspace preview</span>
          <strong>Balanced for clarity automatically</strong>
        </div>
        <span className="brand-preview-live">
          <i /> Live
        </span>
      </div>
      <div
        className="brand-preview"
        data-business-theme
        style={brandThemeStyle(tokens)}
      >
        <aside className="brand-preview-sidebar">
          <div className="brand-preview-logo">
            <BusinessBrandMark
              businessName={businessName}
              identity={previewIdentity}
            />
            <span>{businessName || "Your business"}</span>
          </div>
          <div className="brand-preview-nav active">
            <LayoutDashboard /> Dashboard
          </div>
          <div className="brand-preview-nav">
            <Bot /> AI Team
          </div>
          <div className="brand-preview-nav">
            <BarChart3 /> Analytics
          </div>
        </aside>
        <div className="brand-preview-main">
          <div className="brand-preview-topline">
            <div>
              <span>Good morning</span>
              <strong>{businessName || "Your business"}</strong>
            </div>
            <i />
          </div>
          <div className="brand-preview-actions">
            <button className="brand-preview-primary">Primary action</button>
            <button className="brand-preview-secondary">Secondary</button>
            <span className="brand-preview-badge">
              <Sparkles /> AI active
            </span>
          </div>
          <div className="brand-preview-cards">
            <div className="brand-preview-card dashboard">
              <span>Revenue this month</span>
              <strong>$24,820</strong>
              <div>
                <i />
                <i />
                <i />
                <i />
                <i />
              </div>
            </div>
            <div className="brand-preview-card agent">
              <div className="brand-preview-agent-icon">
                <Bot />
              </div>
              <div>
                <strong>AI Business Manager</strong>
                <span>12 tasks completed</span>
              </div>
              <i className="brand-preview-status" />
            </div>
          </div>
        </div>
      </div>
      <p className="brand-preview-note">
        Neutral surfaces stay clean while your brand guides actions, focus, and
        AI accents.
      </p>
    </div>
  );
}

export function BrandingEditor({
  businessName,
  value,
  onChange,
  legacyTheme = "green",
}: {
  businessName: string;
  value: BrandIdentityDraft;
  onChange: (next: BrandIdentityDraft) => void;
  legacyTheme?: "green" | "navy";
}) {
  const fallback = getDefaultBrandIdentity(legacyTheme);
  const resolved = resolveBrandIdentity(
    {
      primaryColor: normalizeHex(value.primaryColor) ?? fallback.primaryColor,
      secondaryColor: normalizeHex(value.secondaryColor) ?? undefined,
      accentColor: normalizeHex(value.accentColor) ?? undefined,
    },
    legacyTheme,
  );

  return (
    <div className="branding-editor-grid">
      <div className="branding-config">
        <div className="branding-section-heading">
          <div className="branding-section-icon">
            <Palette />
          </div>
          <div>
            <strong>Brand identity</strong>
            <span>
              Your choices are refined into accessible workspace tokens.
            </span>
          </div>
        </div>

        <LogoUploader
          businessName={businessName}
          value={value}
          onChange={onChange}
        />

        <div className="brand-presets" aria-label="Brand color presets">
          <div className="brand-setting-label">Starting presets</div>
          <div className="brand-preset-grid">
            {BRAND_PRESETS.map((preset) => {
              const selected =
                normalizeHex(value.primaryColor) === preset.primaryColor &&
                normalizeHex(value.secondaryColor) === preset.secondaryColor &&
                normalizeHex(value.accentColor) === preset.accentColor;
              return (
                <button
                  type="button"
                  className={cx("brand-preset", selected && "selected")}
                  onClick={() => onChange({ ...value, ...preset })}
                  aria-pressed={selected}
                  key={preset.name}
                >
                  <span className="brand-preset-swatches">
                    <i style={{ background: preset.primaryColor }} />
                    <i style={{ background: preset.secondaryColor }} />
                    <i style={{ background: preset.accentColor }} />
                  </span>
                  <span>{preset.name}</span>
                  {selected && <Check />}
                </button>
              );
            })}
          </div>
        </div>

        <div className="brand-color-grid">
          <ColorField
            id="brand-primary"
            label="Primary brand color"
            value={value.primaryColor}
            fallback={fallback.primaryColor}
            required
            onChange={(primaryColor) => onChange({ ...value, primaryColor })}
          />
          <ColorField
            id="brand-secondary"
            label="Secondary color"
            value={value.secondaryColor}
            fallback={resolved.secondary}
            onChange={(secondaryColor) =>
              onChange({ ...value, secondaryColor })
            }
          />
          <ColorField
            id="brand-accent"
            label="Accent color"
            value={value.accentColor}
            fallback={resolved.accent}
            onChange={(accentColor) => onChange({ ...value, accentColor })}
          />
        </div>
      </div>
      <BrandThemePreview
        businessName={businessName}
        value={value}
        legacyTheme={legacyTheme}
      />
    </div>
  );
}

export function brandingDraftHasErrors(value: BrandIdentityDraft) {
  return (
    !isValidHex(value.primaryColor) ||
    Boolean(value.secondaryColor && !isValidHex(value.secondaryColor)) ||
    Boolean(value.accentColor && !isValidHex(value.accentColor))
  );
}
