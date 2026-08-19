import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import {
  CreateBusinessResponse,
  CreateBusinessBody,
  ListBusinessesResponse,
} from "@workspace/api-zod";

export type Business = import("@workspace/api-zod").Business;
export type BusinessInput = import("@workspace/api-zod").BusinessInput;

const storePath = path.resolve(
  process.cwd(),
  "artifacts/api-server/data/businesses.json",
);

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
      { id: "fresh-eggs", name: "Fresh Eggs", price: 6, availability: "In stock" },
      { id: "organic-tomatoes", name: "Organic Tomatoes", price: 4, availability: "In stock" },
      { id: "raw-honey", name: "Raw Honey", price: 12, availability: "In stock" },
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
      { id: "downtown-loft", name: "Downtown Loft", price: 485000, availability: "Available" },
      { id: "oak-hills-home", name: "Oak Hills Home", price: 725000, availability: "Open house Saturday" },
      { id: "river-road-lot", name: "River Road Lot", price: 210000, availability: "Coming soon" },
    ],
    onboardingComplete: true,
    theme: "navy",
  },
];

function readBusinesses(): Business[] {
  try {
    if (!existsSync(storePath)) return seedBusinesses;
    const raw = JSON.parse(readFileSync(storePath, "utf8")) as unknown;
    const parsed = ListBusinessesResponse.safeParse(raw);
    return parsed.success ? parsed.data : seedBusinesses;
  } catch {
    return seedBusinesses;
  }
}

let businesses = readBusinesses();

function persist() {
  mkdirSync(path.dirname(storePath), { recursive: true });
  writeFileSync(storePath, JSON.stringify(businesses, null, 2));
}

export function listBusinesses() {
  return businesses;
}

export function getBusiness(id: string) {
  return businesses.find((business) => business.id === id);
}

export function createBusiness(input: BusinessInput): Business {
  const id = `${input.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${Date.now()}`;
  const business: Business = {
    id,
    name: input.name,
    industry: input.industry,
    website: input.website ?? "",
    location: input.location ?? "",
    timezone: input.timezone ?? "UTC",
    currency: input.currency ?? "USD · $",
    description: input.description ?? "",
    tone: input.tone ?? "",
    avoidKeywords: input.avoidKeywords ?? "",
    connectedChannels: input.connectedChannels ?? [],
    products: input.products ?? [],
    onboardingComplete: input.onboardingComplete ?? true,
    theme: input.theme ?? (input.industry === "Real Estate" ? "navy" : "green"),
  };
  businesses = [...businesses, business];
  persist();
  return business;
}

export function updateBusiness(id: string, input: BusinessInput): Business | undefined {
  const current = getBusiness(id);
  if (!current) return undefined;
  const updated: Business = {
    ...current,
    ...input,
    website: input.website ?? current.website,
    location: input.location ?? current.location,
    timezone: input.timezone ?? current.timezone,
    currency: input.currency ?? current.currency,
    description: input.description ?? current.description,
    tone: input.tone ?? current.tone,
    avoidKeywords: input.avoidKeywords ?? current.avoidKeywords,
    connectedChannels: input.connectedChannels ?? current.connectedChannels,
    products: input.products ?? current.products,
    onboardingComplete: input.onboardingComplete ?? current.onboardingComplete,
    theme: input.theme ?? current.theme,
  };
  businesses = businesses.map((business) => business.id === id ? updated : business);
  persist();
  return updated;
}