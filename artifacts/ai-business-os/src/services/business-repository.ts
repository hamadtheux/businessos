import type { Business, BusinessInput } from "@workspace/api-client-react";

const VERSION = 1;
const INDEX_KEY = `ai-business-os:business-index:v${VERSION}`;
const memory = new Map<string, Business>();

const seedBusinesses: Business[] = [
  {
    id: "green-valley-farms",
    name: "Green Valley Farms",
    industry: "Farm/Agriculture",
    website: "greenvalleyfarms.co",
    location: "Sonoma County, California",
    timezone: "America/Los_Angeles",
    currency: "USD · $",
    description:
      "A family farm growing seasonal produce, fresh eggs, and raw honey for local families.",
    tone: "Warm, grounded, and useful",
    avoidKeywords: "cheap, best ever, hurry, guaranteed",
    connectedChannels: ["WhatsApp", "Instagram", "Email", "Stripe"],
    products: [
      {
        id: "fresh-eggs",
        name: "Fresh Eggs",
        price: 6,
        availability: "In stock",
      },
      {
        id: "organic-tomatoes",
        name: "Organic Tomatoes",
        price: 4,
        availability: "In stock",
      },
      {
        id: "raw-honey",
        name: "Raw Honey",
        price: 12,
        availability: "In stock",
      },
    ],
    onboardingComplete: true,
    theme: "green",
  },
  {
    id: "abc-real-estate",
    name: "ABC Real Estate",
    industry: "Real Estate",
    website: "abcrealestate.com",
    location: "Austin, Texas",
    timezone: "America/Chicago",
    currency: "USD · $",
    description:
      "A relationship-led real estate team helping families find homes and investment properties.",
    tone: "Clear, confident, and welcoming",
    avoidKeywords: "guaranteed, perfect, once-in-a-lifetime",
    connectedChannels: ["Email", "Stripe"],
    products: [
      {
        id: "downtown-loft",
        name: "Downtown Loft",
        price: 485000,
        availability: "Available",
      },
      {
        id: "oak-hills-home",
        name: "Oak Hills Home",
        price: 725000,
        availability: "Open house Saturday",
      },
      {
        id: "river-road-lot",
        name: "River Road Lot",
        price: 210000,
        availability: "Coming soon",
      },
    ],
    onboardingComplete: true,
    theme: "navy",
  },
];

function recordKey(businessId: string) {
  return `ai-business-os:business:${businessId}:v${VERSION}`;
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function readIndex() {
  try {
    const raw = localStorage.getItem(INDEX_KEY);
    return raw ? (JSON.parse(raw) as string[]) : [];
  } catch {
    return [];
  }
}

function readRecord(id: string) {
  const cached = memory.get(id);
  if (cached) return clone(cached);
  try {
    const raw = localStorage.getItem(recordKey(id));
    if (!raw) return undefined;
    const business = JSON.parse(raw) as Business;
    memory.set(id, business);
    return clone(business);
  } catch {
    return undefined;
  }
}

function businessFromInput(
  id: string,
  input: BusinessInput,
  current?: Business,
): Business {
  return {
    id,
    name: input.name,
    industry: input.industry,
    website: input.website ?? current?.website ?? "",
    location: input.location ?? current?.location ?? "",
    timezone: input.timezone ?? current?.timezone ?? "UTC",
    currency: input.currency ?? current?.currency ?? "USD · $",
    description: input.description ?? current?.description ?? "",
    tone: input.tone ?? current?.tone ?? "",
    avoidKeywords: input.avoidKeywords ?? current?.avoidKeywords ?? "",
    connectedChannels:
      input.connectedChannels ?? current?.connectedChannels ?? [],
    products: input.products ?? current?.products ?? [],
    onboardingComplete:
      input.onboardingComplete ?? current?.onboardingComplete ?? true,
    theme:
      input.theme ??
      current?.theme ??
      (input.industry === "Real Estate" ? "navy" : "green"),
  };
}

function persistBusiness(business: Business) {
  const ids = readIndex();
  const nextIds = ids.includes(business.id) ? ids : [...ids, business.id];
  try {
    localStorage.setItem(recordKey(business.id), JSON.stringify(business));
    localStorage.setItem(INDEX_KEY, JSON.stringify(nextIds));
  } catch (error) {
    throw new Error(
      error instanceof Error && error.message
        ? `The workspace could not be saved in this browser: ${error.message}`
        : "The workspace could not be saved in this browser.",
    );
  }
  memory.set(business.id, clone(business));
  return clone(business);
}

function initializeSeeds() {
  if (readIndex().length) return;
  try {
    seedBusinesses.forEach((business) => {
      localStorage.setItem(recordKey(business.id), JSON.stringify(business));
      memory.set(business.id, clone(business));
    });
    localStorage.setItem(
      INDEX_KEY,
      JSON.stringify(seedBusinesses.map((business) => business.id)),
    );
  } catch {
    seedBusinesses.forEach((business) =>
      memory.set(business.id, clone(business)),
    );
  }
}

export const businessRepository = {
  list() {
    initializeSeeds();
    const ids = readIndex();
    if (!ids.length) return seedBusinesses.map(clone);
    return ids
      .map((id) => readRecord(id))
      .filter((business): business is Business => Boolean(business));
  },

  get(id: string) {
    initializeSeeds();
    return readRecord(id);
  },

  upsert(id: string, input: BusinessInput) {
    initializeSeeds();
    return persistBusiness(businessFromInput(id, input, readRecord(id)));
  },

  update(id: string, input: BusinessInput) {
    initializeSeeds();
    const current = readRecord(id);
    if (!current)
      throw new Error("This business workspace could not be found.");
    return persistBusiness(businessFromInput(id, input, current));
  },
};
