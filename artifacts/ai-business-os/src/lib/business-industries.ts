export type BusinessIndustryGroup =
  | "agriculture"
  | "real_estate"
  | "commerce"
  | "healthcare"
  | "professional_services"
  | "other";

export type BusinessIndustryDefinition = {
  readonly label: string;
  readonly backendCode: string;
  readonly group: BusinessIndustryGroup;
  readonly isHealthcare: boolean;
  readonly supportsScheduling: boolean;
  readonly defaultTheme: "green" | "navy";
};

export const BUSINESS_INDUSTRY_DEFINITIONS = {
  "Farm/Agriculture": {
    label: "Farm/Agriculture",
    backendCode: "farm/agriculture",
    group: "agriculture",
    isHealthcare: false,
    supportsScheduling: false,
    defaultTheme: "green",
  },

  "Real Estate": {
    label: "Real Estate",
    backendCode: "real estate",
    group: "real_estate",
    isHealthcare: false,
    supportsScheduling: false,
    defaultTheme: "navy",
  },

  "E-commerce": {
    label: "E-commerce",
    backendCode: "e-commerce",
    group: "commerce",
    isHealthcare: false,
    supportsScheduling: false,
    defaultTheme: "green",
  },

  Hospital: {
    label: "Hospital",
    backendCode: "hospital",
    group: "healthcare",
    isHealthcare: true,
    supportsScheduling: true,
    defaultTheme: "green",
  },

  Clinic: {
    label: "Clinic",
    backendCode: "clinic",
    group: "healthcare",
    isHealthcare: true,
    supportsScheduling: true,
    defaultTheme: "green",
  },

  "Medical Practice": {
    label: "Medical Practice",
    backendCode: "medical practice",
    group: "healthcare",
    isHealthcare: true,
    supportsScheduling: true,
    defaultTheme: "green",
  },

  Dental: {
    label: "Dental",
    backendCode: "dental",
    group: "healthcare",
    isHealthcare: true,
    supportsScheduling: true,
    defaultTheme: "green",
  },

  "Professional Services": {
    label: "Professional Services",
    backendCode: "professional services",
    group: "professional_services",
    isHealthcare: false,
    supportsScheduling: true,
    defaultTheme: "green",
  },

  Other: {
    label: "Other",
    backendCode: "other",
    group: "other",
    isHealthcare: false,
    supportsScheduling: false,
    defaultTheme: "green",
  },
} as const satisfies Record<string, BusinessIndustryDefinition>;

export type OnboardingIndustry =
  keyof typeof BUSINESS_INDUSTRY_DEFINITIONS;

export const ONBOARDING_INDUSTRIES = Object.keys(
  BUSINESS_INDUSTRY_DEFINITIONS,
) as OnboardingIndustry[];

export const BUSINESS_INDUSTRY_BACKEND_ALIASES = {
  agriculture: "Farm/Agriculture",
  farm: "Farm/Agriculture",
  "farm/agriculture": "Farm/Agriculture",

  "real estate": "Real Estate",
  "real-estate": "Real Estate",

  ecommerce: "E-commerce",
  "e commerce": "E-commerce",
  "e-commerce": "E-commerce",

  hospital: "Hospital",
  clinic: "Clinic",
  medical: "Medical Practice",
  "medical practice": "Medical Practice",
  dental: "Dental",

  "professional service": "Professional Services",
  "professional services": "Professional Services",

  other: "Other",
} as const satisfies Record<string, OnboardingIndustry>;

export function normalizeBusinessIndustryBackendValue(
  value: string | null | undefined,
): string {
  return (value ?? "").trim().toLowerCase().replace(/\s+/g, " ");
}

export function businessIndustryLabelFromBackendCode(
  value: string | null | undefined,
): OnboardingIndustry | undefined {
  const normalized = normalizeBusinessIndustryBackendValue(value);

  if (!normalized) {
    return undefined;
  }

  const alias = BUSINESS_INDUSTRY_BACKEND_ALIASES[
    normalized as keyof typeof BUSINESS_INDUSTRY_BACKEND_ALIASES
  ];

  if (alias) {
    return alias;
  }

  return ONBOARDING_INDUSTRIES.find(
    (industry) =>
      BUSINESS_INDUSTRY_DEFINITIONS[industry].backendCode === normalized,
  );
}

export function getBusinessIndustryDefinition(
  industry: string | null | undefined,
): BusinessIndustryDefinition | undefined {
  if (!industry || !(industry in BUSINESS_INDUSTRY_DEFINITIONS)) {
    return undefined;
  }

  return BUSINESS_INDUSTRY_DEFINITIONS[
    industry as OnboardingIndustry
  ];
}

export function businessIndustrySupportsScheduling(
  industry: string | null | undefined,
): boolean {
  return getBusinessIndustryDefinition(industry)?.supportsScheduling ?? false;
}

export function isHealthcareIndustry(
  industry: string | null | undefined,
): boolean {
  return getBusinessIndustryDefinition(industry)?.isHealthcare ?? false;
}

export function businessIndustryBackendCode(
  industry: OnboardingIndustry,
): string {
  return BUSINESS_INDUSTRY_DEFINITIONS[industry].backendCode;
}

export function businessIndustryDefaultTheme(
  industry: string | null | undefined,
): "green" | "navy" {
  return getBusinessIndustryDefinition(industry)?.defaultTheme ?? "green";
}
