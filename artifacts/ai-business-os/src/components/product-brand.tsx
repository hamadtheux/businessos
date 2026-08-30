import { PRODUCT_LOGO_PATH, PRODUCT_NAME } from "@/config/brand";
import { cx } from "@/lib/product-utils";

export type ProductLogoSize = "sm" | "md" | "lg";

export function ProductLogo({
  className,
  decorative = false,
  size = "md",
}: {
  className?: string;
  decorative?: boolean;
  size?: ProductLogoSize;
}) {
  return (
    <span className={cx("product-logo", `product-logo-${size}`, className)}>
      <img
        src={PRODUCT_LOGO_PATH}
        alt={decorative ? "" : `${PRODUCT_NAME} logo`}
      />
    </span>
  );
}

export function ProductBrand({
  className,
  logoSize = "md",
  tagline,
}: {
  className?: string;
  logoSize?: ProductLogoSize;
  tagline?: string;
}) {
  return (
    <div className={cx("brand product-brand", className)}>
      <ProductLogo decorative size={logoSize} />
      <div className="brand-text">
        <div className="brand-copy">{PRODUCT_NAME}</div>
        {tagline && <div className="brand-sub">{tagline}</div>}
      </div>
    </div>
  );
}
