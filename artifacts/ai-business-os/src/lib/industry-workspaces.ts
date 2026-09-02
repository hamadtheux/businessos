import type { OnboardingIndustry } from "./business-industries.ts";

export type WorkspaceModule =
  | "dashboard"
  | "ai_command_center"
  | "daily_report"
  | "conversations"
  | "support"
  | "orders"
  | "customers"
  | "crm"
  | "scheduling"
  | "catalog"
  | "website_chatbot"
  | "marketing_cmo"
  | "ai_agents"
  | "automations"
  | "approvals"
  | "opportunities"
  | "analytics"
  | "competitors"
  | "trends"
  | "integrations"
  | "business_brain"
  | "audit";

export type IndustryTerminology = {
  readonly customerSingular: string;
  readonly customerPlural: string;
  readonly providerSingular: string;
  readonly providerPlural: string;
  readonly serviceSingular: string;
  readonly servicePlural: string;
  readonly bookingSingular: string;
  readonly bookingPlural: string;
  readonly bookingActionLabel: string;
  readonly schedulingLabel: string;
  readonly crmLabel: string;
};

export type IndustryDashboardVariant =
  | "agriculture"
  | "real_estate"
  | "commerce"
  | "healthcare"
  | "professional_services"
  | "generic";

export type IndustryCrmStage =
  | "new"
  | "qualified"
  | "contacted"
  | "viewing"
  | "proposal"
  | "won"
  | "lost";

export type IndustryCrmConfiguration = {
  readonly stages: readonly IndustryCrmStage[];
  readonly stageLabels: Readonly<
    Partial<Record<IndustryCrmStage, string>>
  >;
};

export type IndustryWorkspaceProfile = {
  readonly industry: OnboardingIndustry;
  readonly dashboardVariant: IndustryDashboardVariant;
  readonly visibleModules: readonly WorkspaceModule[];
  readonly terminology: IndustryTerminology;
  readonly crm: IndustryCrmConfiguration;
  readonly catalogRoute: "/products" | "/properties" | null;
  readonly catalogLabel: string | null;
};

const COMMON_AI_MODULES = [
  "dashboard",
  "ai_command_center",
  "daily_report",
  "conversations",
  "support",
  "website_chatbot",
  "ai_agents",
  "automations",
  "approvals",
  "opportunities",
  "analytics",
  "integrations",
  "business_brain",
  "audit",
] as const satisfies readonly WorkspaceModule[];

const MARKETING_MODULES = [
  "marketing_cmo",
  "competitors",
  "trends",
] as const satisfies readonly WorkspaceModule[];

const HEALTHCARE_MODULES = [
  ...COMMON_AI_MODULES,
  "customers",
  "crm",
  "scheduling",
  ...MARKETING_MODULES,
] as const satisfies readonly WorkspaceModule[];

const PROFESSIONAL_SERVICE_MODULES = [
  ...COMMON_AI_MODULES,
  "customers",
  "crm",
  "scheduling",
  ...MARKETING_MODULES,
] as const satisfies readonly WorkspaceModule[];

const STANDARD_CRM: IndustryCrmConfiguration = {
  stages: [
    "new",
    "qualified",
    "contacted",
    "proposal",
    "won",
    "lost",
  ],
  stageLabels: {
    new: "New",
    qualified: "Qualified",
    contacted: "Contacted",
    proposal: "Proposal",
    won: "Won",
    lost: "Lost",
  },
};

const REAL_ESTATE_CRM: IndustryCrmConfiguration = {
  stages: [
    "new",
    "qualified",
    "contacted",
    "viewing",
    "proposal",
    "won",
    "lost",
  ],
  stageLabels: {
    new: "New",
    qualified: "Qualified",
    contacted: "Contacted",
    viewing: "Viewing",
    proposal: "Proposal",
    won: "Won",
    lost: "Lost",
  },
};

const HEALTHCARE_CRM: IndustryCrmConfiguration = {
  stages: [
    "new",
    "qualified",
    "contacted",
    "proposal",
    "won",
    "lost",
  ],
  stageLabels: {
    new: "New inquiry",
    qualified: "Qualified",
    contacted: "Contacted",
    proposal: "Care discussion",
    won: "Converted",
    lost: "Closed",
  },
};

const PROFESSIONAL_SERVICES_CRM: IndustryCrmConfiguration = {
  stages: [
    "new",
    "qualified",
    "contacted",
    "proposal",
    "won",
    "lost",
  ],
  stageLabels: {
    new: "New lead",
    qualified: "Qualified",
    contacted: "Contacted",
    proposal: "Proposal",
    won: "Won",
    lost: "Lost",
  },
};

export const INDUSTRY_WORKSPACE_PROFILES = {
  "Farm/Agriculture": {
    industry: "Farm/Agriculture",
    dashboardVariant: "agriculture",
    visibleModules: [
      ...COMMON_AI_MODULES,
      "orders",
      "customers",
      "crm",
      "catalog",
      ...MARKETING_MODULES,
    ],
    terminology: {
      customerSingular: "Customer",
      customerPlural: "Customers",
      providerSingular: "Team Member",
      providerPlural: "Team Members",
      serviceSingular: "Product",
      servicePlural: "Products",
      bookingSingular: "Booking",
      bookingPlural: "Bookings",
      bookingActionLabel: "Create booking",
      schedulingLabel: "Scheduling",
      crmLabel: "Leads & CRM",
    },
    crm: STANDARD_CRM,
    catalogRoute: "/products",
    catalogLabel: "Products",
  },

  "Real Estate": {
    industry: "Real Estate",
    dashboardVariant: "real_estate",
    visibleModules: [
      ...COMMON_AI_MODULES,
      "customers",
      "crm",
      ...MARKETING_MODULES,
    ],
    terminology: {
      customerSingular: "Contact",
      customerPlural: "Contacts",
      providerSingular: "Agent",
      providerPlural: "Agents",
      serviceSingular: "Property",
      servicePlural: "Properties",
      bookingSingular: "Viewing",
      bookingPlural: "Viewings",
      bookingActionLabel: "Schedule viewing",
      schedulingLabel: "Viewings",
      crmLabel: "Leads & Pipeline",
    },
    crm: REAL_ESTATE_CRM,
    catalogRoute: null,
    catalogLabel: null,
  },

  "E-commerce": {
    industry: "E-commerce",
    dashboardVariant: "commerce",
    visibleModules: [
      ...COMMON_AI_MODULES,
      "orders",
      "customers",
      "crm",
      "catalog",
      ...MARKETING_MODULES,
    ],
    terminology: {
      customerSingular: "Customer",
      customerPlural: "Customers",
      providerSingular: "Team Member",
      providerPlural: "Team Members",
      serviceSingular: "Product",
      servicePlural: "Products",
      bookingSingular: "Booking",
      bookingPlural: "Bookings",
      bookingActionLabel: "Create booking",
      schedulingLabel: "Scheduling",
      crmLabel: "Leads & CRM",
    },
    crm: STANDARD_CRM,
    catalogRoute: "/products",
    catalogLabel: "Products",
  },

  Hospital: {
    industry: "Hospital",
    dashboardVariant: "healthcare",
    visibleModules: HEALTHCARE_MODULES,
    terminology: {
      customerSingular: "Patient",
      customerPlural: "Patients",
      providerSingular: "Doctor",
      providerPlural: "Doctors",
      serviceSingular: "Service",
      servicePlural: "Services",
      bookingSingular: "Appointment",
      bookingPlural: "Appointments",
      bookingActionLabel: "Book appointment",
      schedulingLabel: "Appointments",
      crmLabel: "Patient Leads & CRM",
    },
    crm: HEALTHCARE_CRM,
    catalogRoute: null,
    catalogLabel: null,
  },

  Clinic: {
    industry: "Clinic",
    dashboardVariant: "healthcare",
    visibleModules: HEALTHCARE_MODULES,
    terminology: {
      customerSingular: "Patient",
      customerPlural: "Patients",
      providerSingular: "Doctor",
      providerPlural: "Doctors",
      serviceSingular: "Service",
      servicePlural: "Services",
      bookingSingular: "Appointment",
      bookingPlural: "Appointments",
      bookingActionLabel: "Book appointment",
      schedulingLabel: "Appointments",
      crmLabel: "Patient Leads & CRM",
    },
    crm: HEALTHCARE_CRM,
    catalogRoute: null,
    catalogLabel: null,
  },

  "Medical Practice": {
    industry: "Medical Practice",
    dashboardVariant: "healthcare",
    visibleModules: HEALTHCARE_MODULES,
    terminology: {
      customerSingular: "Patient",
      customerPlural: "Patients",
      providerSingular: "Doctor",
      providerPlural: "Doctors",
      serviceSingular: "Service",
      servicePlural: "Services",
      bookingSingular: "Appointment",
      bookingPlural: "Appointments",
      bookingActionLabel: "Book appointment",
      schedulingLabel: "Appointments",
      crmLabel: "Patient Leads & CRM",
    },
    crm: HEALTHCARE_CRM,
    catalogRoute: null,
    catalogLabel: null,
  },

  Dental: {
    industry: "Dental",
    dashboardVariant: "healthcare",
    visibleModules: HEALTHCARE_MODULES,
    terminology: {
      customerSingular: "Patient",
      customerPlural: "Patients",
      providerSingular: "Dentist",
      providerPlural: "Dentists",
      serviceSingular: "Dental Service",
      servicePlural: "Dental Services",
      bookingSingular: "Appointment",
      bookingPlural: "Appointments",
      bookingActionLabel: "Book appointment",
      schedulingLabel: "Appointments",
      crmLabel: "Patient Leads & CRM",
    },
    crm: HEALTHCARE_CRM,
    catalogRoute: null,
    catalogLabel: null,
  },

  "Professional Services": {
    industry: "Professional Services",
    dashboardVariant: "professional_services",
    visibleModules: PROFESSIONAL_SERVICE_MODULES,
    terminology: {
      customerSingular: "Client",
      customerPlural: "Clients",
      providerSingular: "Service Provider",
      providerPlural: "Service Providers",
      serviceSingular: "Service",
      servicePlural: "Services",
      bookingSingular: "Booking",
      bookingPlural: "Bookings",
      bookingActionLabel: "Create booking",
      schedulingLabel: "Bookings",
      crmLabel: "Clients & CRM",
    },
    crm: PROFESSIONAL_SERVICES_CRM,
    catalogRoute: null,
    catalogLabel: null,
  },

  Other: {
    industry: "Other",
    dashboardVariant: "generic",
    visibleModules: [
      ...COMMON_AI_MODULES,
      "orders",
      "customers",
      "crm",
      "catalog",
      ...MARKETING_MODULES,
    ],
    terminology: {
      customerSingular: "Customer",
      customerPlural: "Customers",
      providerSingular: "Service Provider",
      providerPlural: "Service Providers",
      serviceSingular: "Product / Service",
      servicePlural: "Products & Services",
      bookingSingular: "Booking",
      bookingPlural: "Bookings",
      bookingActionLabel: "Create booking",
      schedulingLabel: "Scheduling",
      crmLabel: "Leads & CRM",
    },
    crm: STANDARD_CRM,
    catalogRoute: "/products",
    catalogLabel: "Products & Services",
  },
} as const satisfies Record<OnboardingIndustry, IndustryWorkspaceProfile>;

export function getIndustryWorkspaceProfile(
  industry: string | null | undefined,
): IndustryWorkspaceProfile {
  if (
    industry &&
    industry in INDUSTRY_WORKSPACE_PROFILES
  ) {
    return INDUSTRY_WORKSPACE_PROFILES[
      industry as OnboardingIndustry
    ];
  }

  return INDUSTRY_WORKSPACE_PROFILES.Other;
}

export function isWorkspaceModuleVisible(
  industry: string | null | undefined,
  module: WorkspaceModule,
): boolean {
  return getIndustryWorkspaceProfile(industry).visibleModules.includes(module);
}

export function getIndustryTerminology(
  industry: string | null | undefined,
): IndustryTerminology {
  return getIndustryWorkspaceProfile(industry).terminology;
}
