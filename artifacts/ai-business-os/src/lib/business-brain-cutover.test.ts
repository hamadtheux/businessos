import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  ApiClient,
  ApiError,
  ApiNetworkError,
} from "../services/api-client.ts";
import {
  createBusinessBrainApi,
  humanizeBusinessBrainError,
  knowledgeValidationFields,
} from "../services/business-brain.ts";
import type {
  BusinessBrainManifest,
  BusinessKnowledgeEntry,
  UserLoginResponse,
  UserPublic,
} from "../services/api-types.ts";
import {
  KNOWLEDGE_CATEGORIES,
  MAX_KNOWLEDGE_CONTENT_LENGTH,
  MAX_KNOWLEDGE_TITLE_LENGTH,
  createBusinessKnowledgeDraft,
  filterBusinessKnowledge,
  isCurrentBusinessBrainResponse,
  knowledgeCreateFromDraft,
  knowledgeUpdateFromDraft,
  validateBusinessKnowledgeDraft,
} from "../features/brain/business-brain-model.ts";
import { workspaceRepository } from "../services/workspace-repository.ts";
import type { WorkspaceData } from "../types/workspace.ts";

const businessA = "20000000-0000-4000-8000-000000000011";
const businessB = "20000000-0000-4000-8000-000000000012";

const user: UserPublic = {
  id: "10000000-0000-4000-8000-000000000011",
  email: "brain-owner@example.test",
  first_name: "Brain",
  last_name: "Owner",
  status: "active",
  is_email_verified: true,
  created_at: "2026-01-01T00:00:00Z",
};

const entry: BusinessKnowledgeEntry = {
  id: "30000000-0000-4000-8000-000000000011",
  business_id: businessA,
  category: "faq",
  title: "Server FAQ",
  content: "Authoritative server content.",
  status: "active",
  source_type: "manual",
  source_reference: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const manifest: BusinessBrainManifest = {
  business_id: businessA,
  source_count: 7,
  source_counts_by_type: {
    business_profile: 1,
    branding: 1,
    appointment_type: 0,
    catalog_item: 4,
    knowledge_entry: 1,
  },
  revision: "a".repeat(64),
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function loginResponse(): UserLoginResponse {
  return {
    access_token: "memory-only-brain-test-token",
    token_type: "bearer",
    expires_in: 900,
    user,
  };
}

async function authenticatedBusinessBrainApi(fetcher: typeof fetch) {
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only-password" });
  return createBusinessBrainApi(client);
}

test("knowledge and manifest load for the active real business", async () => {
  const paths: string[] = [];
  const api = await authenticatedBusinessBrainApi(async (input) => {
    const url = new URL(String(input));
    if (url.pathname.endsWith("/login")) return jsonResponse(loginResponse());
    paths.push(url.pathname);
    return url.pathname.endsWith("/manifest")
      ? jsonResponse(manifest)
      : jsonResponse([entry]);
  });

  assert.deepEqual(await api.listKnowledge(businessA), [entry]);
  assert.deepEqual(await api.getManifest(businessA), manifest);
  assert.deepEqual(paths, [
    `/api/v1/businesses/${businessA}/brain/knowledge`,
    `/api/v1/businesses/${businessA}/brain/manifest`,
  ]);
  assert.equal(manifest.source_counts_by_type.catalog_item, 4);
});

test("empty backend knowledge remains empty without fabricated entries", async () => {
  const api = await authenticatedBusinessBrainApi(async (input) =>
    String(input).endsWith("/login")
      ? jsonResponse(loginResponse())
      : jsonResponse([]),
  );

  assert.deepEqual(await api.listKnowledge(businessA), []);
});

test("business switching scopes requests and stale tenant responses are rejected", async () => {
  const paths: string[] = [];
  const api = await authenticatedBusinessBrainApi(async (input) => {
    if (String(input).endsWith("/login")) return jsonResponse(loginResponse());
    paths.push(new URL(String(input)).pathname);
    return jsonResponse([]);
  });

  await api.listKnowledge(businessA);
  await api.listKnowledge(businessB);

  assert.deepEqual(paths, [
    `/api/v1/businesses/${businessA}/brain/knowledge`,
    `/api/v1/businesses/${businessB}/brain/knowledge`,
  ]);
  assert.equal(
    isCurrentBusinessBrainResponse(businessA, 1, businessB, 2),
    false,
  );
  assert.equal(
    isCurrentBusinessBrainResponse(businessB, 2, businessB, 2),
    true,
  );
  assert.equal(
    isCurrentBusinessBrainResponse(businessB, 1, businessB, 2),
    false,
  );
});

test("business-switch abort remains cancellation instead of a network error", async () => {
  const controller = new AbortController();
  let markStarted!: () => void;
  const started = new Promise<void>((resolve) => {
    markStarted = resolve;
  });
  const abortError = new DOMException(
    "Cancelled old tenant request",
    "AbortError",
  );
  const api = await authenticatedBusinessBrainApi(async (input, init) => {
    if (String(input).endsWith("/login")) return jsonResponse(loginResponse());
    return new Promise<Response>((_resolve, reject) => {
      markStarted();
      init?.signal?.addEventListener("abort", () => reject(abortError), {
        once: true,
      });
    });
  });

  const request = api.listKnowledge(businessA, {}, controller.signal);
  await started;
  controller.abort();

  await assert.rejects(request, (error: unknown) => {
    assert.equal(error, abortError);
    assert.equal(error instanceof ApiNetworkError, false);
    return true;
  });
});

test("category and status are the only supported list query filters", async () => {
  let requestedUrl = "";
  const api = await authenticatedBusinessBrainApi(async (input) => {
    if (String(input).endsWith("/login")) return jsonResponse(loginResponse());
    requestedUrl = String(input);
    return jsonResponse([{ ...entry, status: "archived" }]);
  });

  await api.listKnowledge(businessA, {
    category: "policy",
    status: "archived",
  });

  const url = new URL(requestedUrl);
  assert.equal(url.searchParams.get("category"), "policy");
  assert.equal(url.searchParams.get("status"), "archived");
  assert.equal(url.searchParams.has("search"), false);
});

test("create POST sends only editable fields and trusts the backend ID", async () => {
  let requestBody: Record<string, unknown> = {};
  let method = "";
  const api = await authenticatedBusinessBrainApi(async (input, init) => {
    if (String(input).endsWith("/login")) return jsonResponse(loginResponse());
    method = init?.method ?? "GET";
    requestBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
    return jsonResponse({ ...entry, id: "backend-generated-id" }, 201);
  });

  const created = await api.createKnowledge(businessA, {
    category: "faq",
    title: "FAQ",
    content: "Answer",
    status: "active",
  });

  assert.equal(method, "POST");
  assert.equal(created.id, "backend-generated-id");
  assert.deepEqual(requestBody, {
    category: "faq",
    title: "FAQ",
    content: "Answer",
    status: "active",
  });
  for (const forbidden of [
    "id",
    "business_id",
    "source_type",
    "source_reference",
    "created_at",
    "updated_at",
  ]) {
    assert.equal(Object.hasOwn(requestBody, forbidden), false);
  }
});

test("draft conversion trims authored text and cannot spoof source metadata", () => {
  const payload = knowledgeCreateFromDraft({
    category: "procedure",
    title: "  Closing checklist  ",
    content: "  Complete all handoff steps.  ",
    status: "draft",
  });

  assert.deepEqual(payload, {
    category: "procedure",
    title: "Closing checklist",
    content: "Complete all handoff steps.",
    status: "draft",
  });
  assert.equal("source_type" in payload, false);
  assert.equal("business_id" in payload, false);
});

test("edit builds a PATCH with changed fields only", async () => {
  const changes = knowledgeUpdateFromDraft(entry, {
    ...createBusinessKnowledgeDraft(entry),
    title: " Updated FAQ ",
    content: "Authoritative server content.",
  });
  let request: { method?: string; body?: unknown; path?: string } = {};
  const api = await authenticatedBusinessBrainApi(async (input, init) => {
    if (String(input).endsWith("/login")) return jsonResponse(loginResponse());
    request = {
      method: init?.method,
      body: JSON.parse(String(init?.body)),
      path: new URL(String(input)).pathname,
    };
    return jsonResponse({ ...entry, ...changes });
  });

  const updated = await api.updateKnowledge(businessA, entry.id, changes);

  assert.deepEqual(changes, { title: "Updated FAQ" });
  assert.deepEqual(request, {
    method: "PATCH",
    body: { title: "Updated FAQ" },
    path: `/api/v1/businesses/${businessA}/brain/knowledge/${entry.id}`,
  });
  assert.equal(updated.title, "Updated FAQ");
});

test("archive uses DELETE and restore uses a real status PATCH", async () => {
  const requests: Array<{ method?: string; body?: string }> = [];
  const api = await authenticatedBusinessBrainApi(async (input, init) => {
    if (String(input).endsWith("/login")) return jsonResponse(loginResponse());
    requests.push({ method: init?.method, body: String(init?.body ?? "") });
    if (init?.method === "DELETE") return new Response(null, { status: 204 });
    return jsonResponse({ ...entry, status: "active" });
  });

  await api.archiveKnowledge(businessA, entry.id);
  await api.updateKnowledge(businessA, entry.id, { status: "active" });

  assert.deepEqual(
    requests.map((request) => request.method),
    ["DELETE", "PATCH"],
  );
  assert.deepEqual(JSON.parse(requests[1].body ?? "{}"), {
    status: "active",
  });
});

test("identifiers are encoded in knowledge and manifest URL paths", async () => {
  const urls: string[] = [];
  const api = await authenticatedBusinessBrainApi(async (input) => {
    if (String(input).endsWith("/login")) return jsonResponse(loginResponse());
    urls.push(String(input));
    return String(input).endsWith("/manifest")
      ? jsonResponse({ ...manifest, business_id: "tenant/a" })
      : jsonResponse(entry);
  });

  await api.getKnowledge("tenant/a", "entry/b");
  await api.getManifest("tenant/a");

  assert.match(urls[0], /businesses\/tenant%2Fa\/brain\/knowledge\/entry%2Fb$/);
  assert.match(urls[1], /businesses\/tenant%2Fa\/brain\/manifest$/);
});

test("client validation covers blank and backend-aligned length limits", () => {
  const blank = validateBusinessKnowledgeDraft({
    category: "general",
    title: "   ",
    content: "   ",
    status: "active",
  });
  assert.match(blank.title ?? "", /title/);
  assert.match(blank.content ?? "", /content/);

  const overLimit = validateBusinessKnowledgeDraft({
    category: "general",
    title: "t".repeat(MAX_KNOWLEDGE_TITLE_LENGTH + 1),
    content: "c".repeat(MAX_KNOWLEDGE_CONTENT_LENGTH + 1),
    status: "active",
  });
  assert.match(overLimit.title ?? "", /250/);
  assert.match(overLimit.content ?? "", /50,000/);

  assert.deepEqual(
    validateBusinessKnowledgeDraft({
      category: "general",
      title: "t".repeat(MAX_KNOWLEDGE_TITLE_LENGTH),
      content: "c".repeat(MAX_KNOWLEDGE_CONTENT_LENGTH),
      status: "active",
    }),
    {},
  );
});

test("category contract exactly matches the backend enum", () => {
  assert.deepEqual(
    KNOWLEDGE_CATEGORIES.map((option) => option.value),
    [
      "general",
      "faq",
      "policy",
      "procedure",
      "brand",
      "sales",
      "support",
      "operations",
      "marketing",
    ],
  );
});

test("422 validation is field-level and never exposes Pydantic internals", () => {
  const error = new ApiError(422, {
    detail: [
      {
        loc: ["body", "title"],
        msg: "String should have at most 250 characters [secret internal]",
        type: "string_too_long",
      },
      {
        loc: ["body", "content"],
        msg: "String should have at least 1 character",
        type: "string_too_short",
      },
    ],
  });

  const fields = knowledgeValidationFields(error);
  const message = humanizeBusinessBrainError(error);
  assert.match(fields.title ?? "", /250/);
  assert.match(fields.content ?? "", /50,000/);
  assert.equal(message, "Fix the highlighted details and try again.");
  assert.equal(message.includes("Pydantic"), false);
  assert.equal(message.includes("secret internal"), false);
});

test("403, 404, 503, and network errors use safe retryable UX", () => {
  assert.match(humanizeBusinessBrainError(new ApiError(401, null)), /session/);
  assert.match(humanizeBusinessBrainError(new ApiError(403, null)), /access/);
  assert.match(
    humanizeBusinessBrainError(new ApiError(404, null)),
    /no longer available/,
  );
  assert.match(
    humanizeBusinessBrainError(new ApiError(503, null)),
    /temporarily unavailable/,
  );
  assert.match(humanizeBusinessBrainError(new ApiNetworkError()), /reach/);
  assert.equal(
    humanizeBusinessBrainError(
      new ApiError(500, { detail: "private database diagnostic" }),
    ).includes("private database diagnostic"),
    false,
  );
});

test("text search stays client-side over the loaded tenant entries", () => {
  const second = {
    ...entry,
    id: "second",
    title: "Returns policy",
    content: "Unopened products only.",
  };
  assert.deepEqual(filterBusinessKnowledge([entry, second], "returns"), [
    second,
  ]);
  assert.deepEqual(filterBusinessKnowledge([entry, second], "server content"), [
    entry,
  ]);
});

test("production workspace persistence strips legacy saved brain knowledge", () => {
  const storage = new MemoryStorage();
  Object.defineProperty(globalThis, "localStorage", {
    value: storage,
    configurable: true,
  });
  Object.defineProperty(globalThis, "window", {
    value: { dispatchEvent: () => true },
    configurable: true,
  });
  const businessId = "40000000-0000-4000-8000-000000000088";
  const current = workspaceRepository.get(businessId, "Other");
  const withLegacyKnowledge: WorkspaceData = {
    ...current,
    brainSources: [
      {
        id: "legacy-local-id",
        name: "Private local knowledge",
        category: "FAQs",
        type: "TXT",
        status: "Processed",
        added: "Today",
      },
    ],
  };

  workspaceRepository.set(businessId, withLegacyKnowledge);
  const stored = Array.from({ length: storage.length }, (_, index) =>
    storage.key(index),
  )
    .filter((key): key is string => Boolean(key))
    .map((key) => storage.getItem(key) ?? "")
    .join("\n");

  assert.equal(stored.includes("Private local knowledge"), false);
  assert.equal(stored.includes("legacy-local-id"), false);
  assert.deepEqual(
    workspaceRepository.get(businessId, "Other").brainSources,
    [],
  );

  const legacyBusinessId = "40000000-0000-4000-8000-000000000089";
  const legacyKey = `ai-business-os:workspace:${legacyBusinessId}:v3`;
  storage.setItem(legacyKey, JSON.stringify(withLegacyKnowledge));
  assert.equal(
    storage.getItem(legacyKey)?.includes("Private local knowledge"),
    true,
  );
  assert.deepEqual(
    workspaceRepository.get(legacyBusinessId, "Other").brainSources,
    [],
  );
  assert.equal(
    storage.getItem(legacyKey)?.includes("Private local knowledge"),
    false,
  );
});

test("screen uses real APIs, refreshes manifest after mutations, and has no fake AI", async () => {
  const [page, dialog, service, repository, apiClient, app] = await Promise.all(
    [
      readFile(
        new URL("../features/brain/business-brain-page.tsx", import.meta.url),
        "utf8",
      ),
      readFile(
        new URL(
          "../features/brain/business-knowledge-dialog.tsx",
          import.meta.url,
        ),
        "utf8",
      ),
      readFile(
        new URL("../services/business-brain.ts", import.meta.url),
        "utf8",
      ),
      readFile(
        new URL("../services/workspace-repository.ts", import.meta.url),
        "utf8",
      ),
      readFile(new URL("../services/api-client.ts", import.meta.url), "utf8"),
      readFile(new URL("../App.tsx", import.meta.url), "utf8"),
    ],
  );

  assert.match(page, /businessBrainApi\.listKnowledge/);
  assert.match(page, /businessBrainApi\.getManifest/);
  assert.match(page, /businessBrainApi\.archiveKnowledge/);
  assert.match(page, /businessBrainApi\.updateKnowledge/);
  assert.match(dialog, /businessBrainApi\.createKnowledge/);
  assert.match(dialog, /businessBrainApi\.updateKnowledge/);
  assert.match(page, /reload\(\)/);
  assert.match(page, /AbortController/);
  assert.match(page, /activeBusinessIdRef/);
  assert.doesNotMatch(
    page,
    /useWorkspaceData|brainSources|setTimeout|Date\.now/,
  );
  assert.doesNotMatch(
    page,
    /Upload source|Search your brain|AI has learned|AI trained/,
  );
  assert.doesNotMatch(page, /manifest\.revision/);
  assert.doesNotMatch(service, /\bfetch\s*\(|localStorage|sessionStorage/);
  assert.doesNotMatch(
    dialog,
    /name=["'](?:source_type|source_reference|business_id)/,
  );
  assert.match(
    repository,
    /clientEnvironment\?\.DEV\s*&&\s*clientEnvironment\.VITE_ENABLE_DEMO_DATA\s*===\s*"true"/,
  );
  assert.match(repository, /brainSources:\s*empty\.brainSources/);
  assert.match(
    repository,
    /brainSources:\s*demoWorkspaceDataEnabled\s*\?\s*value\.brainSources\s*:\s*\[\]/,
  );
  assert.doesNotMatch(apiClient, /localStorage|sessionStorage/);
  assert.match(app, /path="\/brain" component=\{BusinessBrainPage\}/);
  assert.doesNotMatch(
    app,
    /path="\/brain" component=\{workspaceModule\(BusinessBrainPage\)\}/,
  );
});

class MemoryStorage implements Storage {
  private values = new Map<string, string>();

  get length() {
    return this.values.size;
  }

  clear() {
    this.values.clear();
  }

  getItem(key: string) {
    return this.values.get(key) ?? null;
  }

  key(index: number) {
    return Array.from(this.values.keys())[index] ?? null;
  }

  removeItem(key: string) {
    this.values.delete(key);
  }

  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
}
