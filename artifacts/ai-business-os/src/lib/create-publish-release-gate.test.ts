import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  localDateTimeToUtcIso,
  platformReadiness,
  updatedPlatformFields,
} from "../features/marketing/create-publish-model.ts";

test("Create & Publish remounts its tenant-local state on business switch", async () => {
  const [wrapper, page] = await Promise.all([
    readFile(new URL("../features/marketing/cmo-page.tsx", import.meta.url), "utf8"),
    readFile(
      new URL("../features/marketing/create-publish-page.tsx", import.meta.url),
      "utf8",
    ),
  ]);

  assert.match(wrapper, /<CreatePublishPage key=\{activeBusinessId \|\| "no-business"\} \/>/);
  for (const state of [
    "contentPackage",
    "media",
    "mediaPreviewUrl",
    "activePlatform",
    "publishProgress",
    "notice",
    "error",
    "draftState",
  ]) {
    assert.match(page, new RegExp(`useState[^\\n]*${state}|${state}[^\\n]*useState`, "i"));
  }
});

test("datetime-local values convert in the business IANA timezone", () => {
  assert.equal(
    localDateTimeToUtcIso("2026-09-15T19:00", "Asia/Karachi"),
    "2026-09-15T14:00:00.000Z",
  );
  assert.equal(
    localDateTimeToUtcIso("2026-01-15T09:30", "America/Los_Angeles"),
    "2026-01-15T17:30:00.000Z",
  );
});

test("DST gaps and invalid timezones fail with controlled errors", () => {
  assert.throws(
    () => localDateTimeToUtcIso("2026-03-08T02:30", "America/Los_Angeles"),
    /does not exist in the business timezone/,
  );
  assert.throws(
    () => localDateTimeToUtcIso("2026-09-15T19:00", "Invalid\/Timezone"),
    /business timezone is invalid/,
  );
});

test("schedule copy describes an internal calendar, not automatic publishing", async () => {
  const page = await readFile(
    new URL("../features/marketing/create-publish-page.tsx", import.meta.url),
    "utf8",
  );

  assert.match(page, /title="Schedule in Content Calendar"/);
  assert.match(page, /This does not publish automatically\./);
  assert.match(page, /disabled=\{pending \|\| Boolean\(timezoneError\)\}/);
  assert.match(page, /\{timezoneError \|\| timeError\}/);
  assert.doesNotMatch(page, /scheduled (?:to|for) (?:Instagram|Facebook|LinkedIn|TikTok|YouTube)/i);
});

test("publishing is limited to real connectors, scopes, and explicit owner approval", async () => {
  const [page, editor] = await Promise.all([
    readFile(
      new URL("../features/marketing/create-publish-page.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../features/marketing/create-publish-editor.tsx", import.meta.url),
      "utf8",
    ),
  ]);

  const definitions = [
    {
      connector_type: "facebook",
      setup_status: "available",
      external_writes_enabled: true,
      capabilities: [
        "read_pages",
        "future_publish_content",
      ],
      future_write_capabilities: [
        "future_publish_content",
      ],
      resource_selection_required: true,
    },
    {
      connector_type: "instagram",
      setup_status: "provider_setup_required",
      external_writes_enabled: true,
      capabilities: [
        "read_accounts",
        "future_publish_content",
      ],
      future_write_capabilities: [
        "future_publish_content",
      ],
      resource_selection_required: true,
    },
  ] as never;

  const connections = [
    {
      connector_type: "facebook",
      status: "connected",
      authentication_state: "authorized",
      health: "healthy",
      scopes_granted: [
        "pages_show_list",
        "pages_read_engagement",
        "pages_manage_posts",
      ],
      selected_resources: [
        {
          resource_type: "facebook_page",
          external_reference: "page-one",
          display_name: "Page One",
        },
      ],
    },
  ] as never;

  assert.deepEqual(
    platformReadiness(
      "facebook",
      definitions,
      connections,
    ),
    {
      state: "connected",
      label: "Connected",
      canPublish: true,
    },
  );

  const missingPublishScope = [
    {
      connector_type: "facebook",
      status: "connected",
      authentication_state: "authorized",
      health: "healthy",
      scopes_granted: [
        "pages_show_list",
        "pages_read_engagement",
      ],
      selected_resources: [
        {
          resource_type: "facebook_page",
          external_reference: "page-one",
          display_name: "Page One",
        },
      ],
    },
  ] as never;

  assert.deepEqual(
    platformReadiness(
      "facebook",
      definitions,
      missingPublishScope,
    ),
    {
      state: "disconnected",
      label: "Reconnect",
      canPublish: false,
    },
  );

  assert.equal(
    platformReadiness(
      "instagram",
      definitions,
      connections,
    ).state,
    "coming_soon",
  );
  assert.equal(
    platformReadiness(
      "instagram",
      definitions,
      connections,
    ).canPublish,
    false,
  );

  for (const platform of [
    "linkedin",
    "tiktok",
    "youtube",
  ] as const) {
    assert.equal(
      platformReadiness(
        platform,
        definitions,
        connections,
      ).state,
      "coming_soon",
    );
    assert.equal(
      platformReadiness(
        platform,
        definitions,
        connections,
      ).canPublish,
      false,
    );
  }

  assert.match(page, /\["owner", "admin"\]\.includes/);
  assert.match(
    page,
    /proposal\.connector_state\s*!==\s*"ready_after_approval"/,
  );
  assert.match(
    page,
    /proposal\.approval_status\s*===\s*"pending"/,
  );
  assert.match(
    page,
    /automationsApi\.approvals\.approve/,
  );
  assert.match(
    page,
    /platform !==\s*"facebook" &&\s*platform !==\s*"instagram"/,
  );
  assert.match(editor, /!canApproveExternal/);
  assert.match(editor, /data-testid="publish-only"/);
  assert.match(editor, /data-testid="publish-run-campaign"/);
});


test("exact selected media survives edits and reloads", async () => {
  const selectedMediaId =
    "22222222-2222-4222-8222-222222222222";

  const updated = updatedPlatformFields(
    {
      id: "content-one",
      channel: "facebook",
      title: "Title",
      body: "Original copy",
      cta: null,
      platform_fields: {
        platform: "facebook",
        caption: "Original copy",
        hashtags: [],
        keywords: [],
        alt_text: null,
        media_disabled: false,
        selected_media_asset_id: selectedMediaId,
      },
    } as never,
    {
      title: "Edited title",
      body: "Edited copy",
      cta: "",
      hashtags: "",
      keywords: "",
      altText: "",
    },
  );

  assert.equal(
    updated.selected_media_asset_id,
    selectedMediaId,
  );
  assert.equal(
    updated.media_disabled,
    false,
  );

  const page = await readFile(
    new URL(
      "../features/marketing/create-publish-page.tsx",
      import.meta.url,
    ),
    "utf8",
  );

  assert.match(
    page,
    /selected_media_asset_id:\s*asset\?\.id \|\| null/,
  );
  assert.match(
    page,
    /owner\.platform_fields\?\.selected_media_asset_id/,
  );
  assert.match(
    page,
    /assets\.find\(\(asset\) => asset\.id === selectedMediaId\)/,
  );
  assert.match(
    page,
    /void persistMediaChoice\(asset\)/,
  );
});
