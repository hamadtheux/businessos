import type { BrandIdentity } from "@/types/business";
import {
  PRODUCT_BRAND_COLORS,
  PRODUCT_BRAND_GRADIENT,
} from "../config/brand.ts";

type Rgb = { r: number; g: number; b: number };
type Hsl = { h: number; s: number; l: number };

export type BrandIdentityDraft = {
  logo?: BrandIdentity["logo"];
  logoUrl?: string;
  primaryColor: string;
  secondaryColor: string;
  accentColor: string;
};

export type BrandThemeTokens = {
  brandPrimary: string;
  brandPrimaryHover: string;
  brandPrimaryActive: string;
  brandPrimarySoft: string;
  brandPrimaryBorder: string;
  brandOnPrimary: string;
  brandPrimaryInk: string;
  brandSecondary: string;
  brandSecondarySoft: string;
  brandOnSecondary: string;
  brandAccent: string;
  brandAccentSoft: string;
  brandOnAccent: string;
  sidebarBackground: string;
  sidebarForeground: string;
  sidebarMutedForeground: string;
  sidebarActiveBackground: string;
  sidebarActiveForeground: string;
  sidebarBorder: string;
  focusRing: string;
  selectionBackground: string;
  selectionForeground: string;
  brandGradient: string;
};

export const BRAND_PRESETS = [
  {
    name: "Forest",
    primaryColor: "#176B45",
    secondaryColor: "#476B59",
    accentColor: "#D36F32",
  },
  {
    name: "Ocean",
    primaryColor: "#0E7490",
    secondaryColor: "#315D76",
    accentColor: "#E07A3F",
  },
  {
    name: "Midnight",
    primaryColor: "#172554",
    secondaryColor: "#38496F",
    accentColor: "#C88A35",
  },
  {
    name: "Royal",
    primaryColor: "#5B3FBB",
    secondaryColor: "#475B91",
    accentColor: "#D15B83",
  },
  {
    name: "Sunset",
    primaryColor: "#C5522F",
    secondaryColor: "#7A4D67",
    accentColor: "#E3A329",
  },
  {
    name: "Graphite",
    primaryColor: "#364152",
    secondaryColor: "#5E6877",
    accentColor: "#C87941",
  },
] as const;

export const DEFAULT_BRAND_IDENTITIES = {
  green: {
    primaryColor: PRODUCT_BRAND_COLORS.primary,
    secondaryColor: PRODUCT_BRAND_COLORS[900],
    accentColor: PRODUCT_BRAND_COLORS.gold,
  },
  navy: {
    primaryColor: "#1E3A8A",
    secondaryColor: "#4B5E8A",
    accentColor: "#F47C35",
  },
} satisfies Record<string, BrandIdentity>;

export function normalizeHex(value: string) {
  const trimmed = value.trim();
  const short = /^#([\da-f]{3})$/i.exec(trimmed);
  if (short) {
    return `#${short[1]
      .split("")
      .map((character) => character.repeat(2))
      .join("")}`.toUpperCase();
  }
  return /^#[\da-f]{6}$/i.test(trimmed) ? trimmed.toUpperCase() : null;
}

export function isValidHex(value: string) {
  return normalizeHex(value) !== null;
}

export function hexToRgb(value: string): Rgb {
  const hex = normalizeHex(value);
  if (!hex) throw new Error(`Invalid HEX color: ${value}`);
  return {
    r: Number.parseInt(hex.slice(1, 3), 16),
    g: Number.parseInt(hex.slice(3, 5), 16),
    b: Number.parseInt(hex.slice(5, 7), 16),
  };
}

function channelLuminance(channel: number) {
  const value = channel / 255;
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

export function relativeLuminance(value: string) {
  const { r, g, b } = hexToRgb(value);
  return (
    0.2126 * channelLuminance(r) +
    0.7152 * channelLuminance(g) +
    0.0722 * channelLuminance(b)
  );
}

export function contrastRatio(first: string, second: string) {
  const light = Math.max(relativeLuminance(first), relativeLuminance(second));
  const dark = Math.min(relativeLuminance(first), relativeLuminance(second));
  return (light + 0.05) / (dark + 0.05);
}

export function safeForeground(background: string) {
  const light = "#FFFFFF";
  const dark = PRODUCT_BRAND_COLORS.text;
  return contrastRatio(background, light) >= contrastRatio(background, dark)
    ? light
    : dark;
}

export function mixHex(first: string, second: string, secondWeight: number) {
  const a = hexToRgb(first);
  const b = hexToRgb(second);
  const weight = Math.min(1, Math.max(0, secondWeight));
  const channel = (start: number, end: number) =>
    Math.round(start * (1 - weight) + end * weight)
      .toString(16)
      .padStart(2, "0");
  return `#${channel(a.r, b.r)}${channel(a.g, b.g)}${channel(a.b, b.b)}`.toUpperCase();
}

function rgbToHsl({ r, g, b }: Rgb): Hsl {
  const red = r / 255;
  const green = g / 255;
  const blue = b / 255;
  const max = Math.max(red, green, blue);
  const min = Math.min(red, green, blue);
  const delta = max - min;
  const lightness = (max + min) / 2;
  let hue = 0;

  if (delta) {
    if (max === red) hue = 60 * (((green - blue) / delta) % 6);
    else if (max === green) hue = 60 * ((blue - red) / delta + 2);
    else hue = 60 * ((red - green) / delta + 4);
  }

  return {
    h: hue < 0 ? hue + 360 : hue,
    s: delta ? delta / (1 - Math.abs(2 * lightness - 1)) : 0,
    l: lightness,
  };
}

function hslToHex({ h, s, l }: Hsl) {
  const chroma = (1 - Math.abs(2 * l - 1)) * s;
  const section = h / 60;
  const x = chroma * (1 - Math.abs((section % 2) - 1));
  const [red, green, blue] =
    section < 1
      ? [chroma, x, 0]
      : section < 2
        ? [x, chroma, 0]
        : section < 3
          ? [0, chroma, x]
          : section < 4
            ? [0, x, chroma]
            : section < 5
              ? [x, 0, chroma]
              : [chroma, 0, x];
  const match = l - chroma / 2;
  const channel = (value: number) =>
    Math.round((value + match) * 255)
      .toString(16)
      .padStart(2, "0");
  return `#${channel(red)}${channel(green)}${channel(blue)}`.toUpperCase();
}

function deriveSecondary(primary: string) {
  const color = rgbToHsl(hexToRgb(primary));
  return hslToHex({
    h: (color.h + 38) % 360,
    s: Math.min(0.48, Math.max(0.24, color.s * 0.58)),
    l: Math.min(0.48, Math.max(0.3, color.l)),
  });
}

function deriveAccent(primary: string) {
  const color = rgbToHsl(hexToRgb(primary));
  const hue = color.h >= 65 && color.h <= 270 ? 27 : (color.h + 155) % 360;
  return hslToHex({ h: hue, s: 0.66, l: 0.52 });
}

function interactionColor(color: string, amount: number) {
  const hsl = rgbToHsl(hexToRgb(color));
  const lightness =
    hsl.l < 0.1 ? Math.min(1, hsl.l + amount) : Math.max(0, hsl.l - amount);
  return hslToHex({ ...hsl, l: lightness });
}

export function getDefaultBrandIdentity(theme: "green" | "navy" = "green") {
  return { ...DEFAULT_BRAND_IDENTITIES[theme] };
}

export function createBrandIdentityDraft(
  identity?: BrandIdentity,
  theme: "green" | "navy" = "green",
): BrandIdentityDraft {
  const fallback = getDefaultBrandIdentity(theme);
  return {
    logo: identity?.logo,
    logoUrl: identity?.logoUrl,
    primaryColor: identity?.primaryColor ?? fallback.primaryColor,
    secondaryColor: identity?.secondaryColor ?? "",
    accentColor: identity?.accentColor ?? "",
  };
}

export function brandIdentityFromDraft(
  draft: BrandIdentityDraft,
): BrandIdentity {
  const primaryColor = normalizeHex(draft.primaryColor);
  if (!primaryColor) throw new Error("Enter a valid primary HEX color.");
  const secondaryColor = draft.secondaryColor
    ? (normalizeHex(draft.secondaryColor) ?? undefined)
    : undefined;
  const accentColor = draft.accentColor
    ? (normalizeHex(draft.accentColor) ?? undefined)
    : undefined;
  return {
    logo: draft.logo,
    logoUrl: draft.logoUrl,
    primaryColor,
    secondaryColor,
    accentColor,
  };
}

export function isBrandIdentityDraftValid(draft: BrandIdentityDraft) {
  return (
    isValidHex(draft.primaryColor) &&
    (!draft.secondaryColor || isValidHex(draft.secondaryColor)) &&
    (!draft.accentColor || isValidHex(draft.accentColor))
  );
}

export function hasCustomBrandColors(
  identity?: Pick<
    BrandIdentity,
    "primaryColor" | "secondaryColor" | "accentColor"
  >,
) {
  return Boolean(
    identity?.primaryColor || identity?.secondaryColor || identity?.accentColor,
  );
}

export function resolveBrandIdentity(
  identity?: BrandIdentity,
  theme: "green" | "navy" = "green",
) {
  const fallback = getDefaultBrandIdentity(theme);
  const useFallbackPalette = !hasCustomBrandColors(identity);
  const primary =
    normalizeHex(identity?.primaryColor ?? "") ?? fallback.primaryColor;
  const secondary =
    normalizeHex(identity?.secondaryColor ?? "") ??
    (useFallbackPalette && fallback.secondaryColor
      ? fallback.secondaryColor
      : deriveSecondary(primary));
  const accent =
    normalizeHex(identity?.accentColor ?? "") ??
    (useFallbackPalette && fallback.accentColor
      ? fallback.accentColor
      : deriveAccent(primary));
  return { primary, secondary, accent };
}

export function deriveBrandTheme(
  identity?: BrandIdentity,
  theme: "green" | "navy" = "green",
): BrandThemeTokens {
  const { primary, secondary, accent } = resolveBrandIdentity(identity, theme);
  const isProductDefault = !hasCustomBrandColors(identity) && theme === "green";
  const primaryHsl = rgbToHsl(hexToRgb(primary));
  const sidebarBackground = isProductDefault
    ? PRODUCT_BRAND_COLORS[950]
    : hslToHex({
        h: primaryHsl.h,
        s: Math.min(0.44, primaryHsl.s),
        l: 0.12,
      });
  const sidebarActiveBackground = isProductDefault
    ? PRODUCT_BRAND_COLORS[100]
    : mixHex("#FFFFFF", primary, 0.28);
  const primaryBorder = isProductDefault
    ? PRODUCT_BRAND_COLORS[300]
    : contrastRatio(primary, "#FFFFFF") < 1.25
      ? "#CBD5E1"
      : mixHex("#FFFFFF", primary, 0.36);
  const primarySoft = isProductDefault
    ? PRODUCT_BRAND_COLORS[100]
    : mixHex("#FFFFFF", primary, 0.11);
  const primaryInk =
    contrastRatio(primary, primarySoft) >= 4.5
      ? primary
      : safeForeground(primarySoft);
  const focusRing = isProductDefault
    ? PRODUCT_BRAND_COLORS.focus
    : contrastRatio(primary, "#FFFFFF") >= 3
      ? primary
      : PRODUCT_BRAND_COLORS.mutedText;
  const selectionBackground =
    contrastRatio(primary, "#FFFFFF") < 1.25
      ? "#DBEAFE"
      : mixHex("#FFFFFF", primary, 0.22);

  return {
    brandPrimary: primary,
    brandPrimaryHover: isProductDefault
      ? PRODUCT_BRAND_COLORS.hover
      : interactionColor(primary, 0.07),
    brandPrimaryActive: isProductDefault
      ? PRODUCT_BRAND_COLORS.active
      : interactionColor(primary, 0.12),
    brandPrimarySoft: primarySoft,
    brandPrimaryBorder: primaryBorder,
    brandOnPrimary: safeForeground(primary),
    brandPrimaryInk: primaryInk,
    brandSecondary: secondary,
    brandSecondarySoft: isProductDefault
      ? PRODUCT_BRAND_COLORS[100]
      : mixHex("#FFFFFF", secondary, 0.11),
    brandOnSecondary: safeForeground(secondary),
    brandAccent: accent,
    brandAccentSoft: isProductDefault
      ? PRODUCT_BRAND_COLORS.goldSoft
      : mixHex("#FFFFFF", accent, 0.12),
    brandOnAccent: safeForeground(accent),
    sidebarBackground,
    sidebarForeground: "#F8FAFC",
    sidebarMutedForeground: mixHex(sidebarBackground, "#FFFFFF", 0.62),
    sidebarActiveBackground,
    sidebarActiveForeground: safeForeground(sidebarActiveBackground),
    sidebarBorder: mixHex(sidebarBackground, "#FFFFFF", 0.12),
    focusRing,
    selectionBackground,
    selectionForeground: safeForeground(selectionBackground),
    brandGradient: isProductDefault
      ? PRODUCT_BRAND_GRADIENT
      : `linear-gradient(135deg, ${primary} 0%, ${interactionColor(primary, 0.12)} 42%, ${accent} 72%, ${secondary} 100%)`,
  };
}

export function brandThemeStyle(tokens: BrandThemeTokens) {
  return {
    "--business-primary": tokens.brandPrimary,
    "--brand-primary": tokens.brandPrimary,
    "--brand-primary-hover": tokens.brandPrimaryHover,
    "--brand-primary-active": tokens.brandPrimaryActive,
    "--brand-primary-soft": tokens.brandPrimarySoft,
    "--brand-primary-border": tokens.brandPrimaryBorder,
    "--brand-on-primary": tokens.brandOnPrimary,
    "--brand-primary-ink": tokens.brandPrimaryInk,
    "--brand-secondary": tokens.brandSecondary,
    "--brand-secondary-soft": tokens.brandSecondarySoft,
    "--brand-on-secondary": tokens.brandOnSecondary,
    "--brand-accent": tokens.brandAccent,
    "--brand-accent-soft": tokens.brandAccentSoft,
    "--brand-on-accent": tokens.brandOnAccent,
    "--brand-sidebar-bg": tokens.sidebarBackground,
    "--brand-sidebar-fg": tokens.sidebarForeground,
    "--brand-sidebar-muted": tokens.sidebarMutedForeground,
    "--brand-sidebar-active-bg": tokens.sidebarActiveBackground,
    "--brand-sidebar-active-fg": tokens.sidebarActiveForeground,
    "--brand-sidebar-border": tokens.sidebarBorder,
    "--brand-focus-ring": tokens.focusRing,
    "--brand-selection-bg": tokens.selectionBackground,
    "--brand-selection-fg": tokens.selectionForeground,
    "--brand-gradient": tokens.brandGradient,
  } as React.CSSProperties;
}
