import { useEffect, useState } from "react";
import { tenantLogoKey, tenantLogoPresentation } from "@/lib/tenant-logo";
import { cx } from "@/lib/product-utils";

export function TenantLogo({
  businessName,
  logoUrl,
  tenantKey,
  className,
}: {
  businessName: string;
  logoUrl?: string | null;
  tenantKey?: string;
  className?: string;
}) {
  const [failedLogoKey, setFailedLogoKey] = useState<string | null>(null);
  const [loadedLogo, setLoadedLogo] = useState<{
    key: string;
    aspectRatio: number;
  } | null>(null);
  const currentLogoKey = tenantLogoKey(businessName, logoUrl, tenantKey);
  const presentation = tenantLogoPresentation(
    businessName,
    logoUrl,
    failedLogoKey,
    tenantKey,
  );

  useEffect(() => {
    setFailedLogoKey((failedKey) =>
      failedKey && failedKey !== currentLogoKey ? null : failedKey,
    );
  }, [currentLogoKey]);

  const logoAspectRatio =
    presentation.kind === "logo" && loadedLogo?.key === presentation.key
      ? loadedLogo.aspectRatio
      : 1;

  return (
    <span
      className={cx(
        "business-brand-mark",
        presentation.kind === "logo" && "has-logo",
        className,
      )}
      data-logo-state={presentation.kind}
      role="img"
      aria-label={`${businessName || "Business"} business identity`}
      style={
        presentation.kind === "logo"
          ? { aspectRatio: logoAspectRatio }
          : undefined
      }
    >
      {presentation.kind === "logo" ? (
        <img
          src={presentation.logoUrl}
          alt=""
          aria-hidden="true"
          onError={(event) => {
            event.currentTarget.hidden = true;
            setFailedLogoKey(presentation.key);
          }}
          onLoad={(event) => {
            const { naturalHeight, naturalWidth } = event.currentTarget;
            if (naturalHeight && naturalWidth) {
              setLoadedLogo({
                key: presentation.key,
                aspectRatio: naturalWidth / naturalHeight,
              });
            }
          }}
        />
      ) : (
        <span aria-hidden="true">{presentation.initials}</span>
      )}
    </span>
  );
}
