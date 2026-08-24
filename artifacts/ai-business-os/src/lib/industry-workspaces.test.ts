import assert from "node:assert/strict";
import test from "node:test";
import { ONBOARDING_INDUSTRIES } from "./business-industries.ts";
import {
  getIndustryTerminology,
  getIndustryWorkspaceProfile,
  INDUSTRY_WORKSPACE_PROFILES,
  isWorkspaceModuleVisible,
} from "./industry-workspaces.ts";

test("every canonical onboarding industry has exactly one workspace profile", () => {
  assert.deepEqual(
    Object.keys(INDUSTRY_WORKSPACE_PROFILES),
    ONBOARDING_INDUSTRIES,
  );
});

test("healthcare workspaces expose care operations and hide commerce modules", () => {
  for (const industry of [
    "Hospital",
    "Clinic",
    "Medical Practice",
    "Dental",
  ] as const) {
    const profile = getIndustryWorkspaceProfile(industry);

    assert.equal(profile.dashboardVariant, "healthcare", industry);
    assert.equal(
      isWorkspaceModuleVisible(industry, "customers"),
      true,
      industry,
    );
    assert.equal(
      isWorkspaceModuleVisible(industry, "crm"),
      true,
      industry,
    );
    assert.equal(
      isWorkspaceModuleVisible(industry, "scheduling"),
      true,
      industry,
    );

    assert.equal(
      isWorkspaceModuleVisible(industry, "orders"),
      false,
      industry,
    );
    assert.equal(
      isWorkspaceModuleVisible(industry, "catalog"),
      false,
      industry,
    );

    assert.equal(profile.catalogRoute, null, industry);
    assert.equal(profile.catalogLabel, null, industry);
  }
});

test("healthcare terminology uses patients and appointment language", () => {
  for (const industry of [
    "Hospital",
    "Clinic",
    "Medical Practice",
  ] as const) {
    const terminology = getIndustryTerminology(industry);

    assert.equal(terminology.customerSingular, "Patient");
    assert.equal(terminology.customerPlural, "Patients");
    assert.equal(terminology.providerSingular, "Doctor");
    assert.equal(terminology.providerPlural, "Doctors");
    assert.equal(terminology.bookingSingular, "Appointment");
    assert.equal(terminology.bookingPlural, "Appointments");
    assert.equal(terminology.bookingActionLabel, "Book appointment");
  }

  const dental = getIndustryTerminology("Dental");

  assert.equal(dental.customerPlural, "Patients");
  assert.equal(dental.providerSingular, "Dentist");
  assert.equal(dental.providerPlural, "Dentists");
  assert.equal(dental.serviceSingular, "Dental Service");
  assert.equal(dental.bookingActionLabel, "Book appointment");
});

test("professional services supports bookings without healthcare semantics", () => {
  const profile = getIndustryWorkspaceProfile("Professional Services");
  const terminology = profile.terminology;

  assert.equal(profile.dashboardVariant, "professional_services");

  assert.equal(
    isWorkspaceModuleVisible("Professional Services", "scheduling"),
    true,
  );
  assert.equal(
    isWorkspaceModuleVisible("Professional Services", "customers"),
    true,
  );
  assert.equal(
    isWorkspaceModuleVisible("Professional Services", "crm"),
    true,
  );

  assert.equal(
    isWorkspaceModuleVisible("Professional Services", "orders"),
    false,
  );
  assert.equal(
    isWorkspaceModuleVisible("Professional Services", "catalog"),
    false,
  );

  assert.equal(terminology.customerSingular, "Client");
  assert.equal(terminology.customerPlural, "Clients");
  assert.equal(terminology.providerSingular, "Service Provider");
  assert.equal(terminology.providerPlural, "Service Providers");
  assert.equal(terminology.bookingSingular, "Booking");
  assert.equal(terminology.bookingPlural, "Bookings");
  assert.equal(terminology.bookingActionLabel, "Create booking");
});

test("commerce keeps products and orders but not scheduling", () => {
  const profile = getIndustryWorkspaceProfile("E-commerce");

  assert.equal(profile.dashboardVariant, "commerce");
  assert.equal(profile.catalogRoute, "/products");
  assert.equal(profile.catalogLabel, "Products");

  assert.equal(
    isWorkspaceModuleVisible("E-commerce", "orders"),
    true,
  );
  assert.equal(
    isWorkspaceModuleVisible("E-commerce", "catalog"),
    true,
  );
  assert.equal(
    isWorkspaceModuleVisible("E-commerce", "scheduling"),
    false,
  );
});

test("real estate keeps viewing CRM without exposing an unfinished properties catalog", () => {
  const profile = getIndustryWorkspaceProfile("Real Estate");

  assert.equal(profile.dashboardVariant, "real_estate");
  assert.equal(profile.catalogRoute, null);
  assert.equal(profile.catalogLabel, null);
  assert.equal(
    isWorkspaceModuleVisible("Real Estate", "catalog"),
    false,
  );
  assert.equal(
    isWorkspaceModuleVisible("Real Estate", "orders"),
    false,
    "commerce orders must stay hidden until a property-transaction domain exists",
  );

  assert.equal(profile.terminology.customerPlural, "Contacts");
  assert.equal(profile.terminology.crmLabel, "Leads & Pipeline");

  assert.equal(profile.crm.stages.includes("viewing"), true);
  assert.equal(profile.crm.stageLabels.viewing, "Viewing");
});

test("viewing is exclusive to the real estate CRM experience", () => {
  for (const industry of ONBOARDING_INDUSTRIES) {
    const profile = getIndustryWorkspaceProfile(industry);

    assert.equal(
      profile.crm.stages.includes("viewing"),
      industry === "Real Estate",
      industry,
    );
  }
});

test("unknown industry values fail safely to the generic workspace", () => {
  const profile = getIndustryWorkspaceProfile("unsupported-industry");

  assert.equal(profile.industry, "Other");
  assert.equal(profile.dashboardVariant, "generic");
  assert.equal(profile.catalogRoute, "/products");
});
