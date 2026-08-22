import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  ApiClient,
  ApiError,
  ApiNetworkError,
  createBusinessApi,
} from "../services/api-client.ts";
import { API_BASE_URL } from "../config/api.ts";
import type {
  UserLoginInput,
  UserLoginResponse,
  UserPublic,
} from "../services/api-types.ts";

const user: UserPublic = {
  id: "10000000-0000-4000-8000-000000000001",
  email: "owner@example.test",
  first_name: "Test",
  last_name: "Owner",
  status: "active",
  is_email_verified: true,
  created_at: "2026-01-01T00:00:00Z",
};

const secondUser: UserPublic = {
  ...user,
  id: "10000000-0000-4000-8000-000000000002",
  email: "second-owner@example.test",
  first_name: "Second",
};

function session(accessToken: string): UserLoginResponse {
  return sessionFor(user, accessToken);
}

function sessionFor(
  account: UserPublic,
  accessToken: string,
): UserLoginResponse {
  return {
    access_token: accessToken,
    token_type: "bearer",
    expires_in: 900,
    user: account,
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

test("development API fallback stays on the localhost browser site", () => {
  assert.equal(API_BASE_URL, "http://localhost:8000");
});

test("registration calls the backend then automatically signs in", async () => {
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input);
    requests.push({ url, init });
    if (url.endsWith("/register")) return jsonResponse(user, 201);
    return jsonResponse(session("memory-session-one"));
  };
  const storageWrites: string[] = [];
  Object.defineProperty(globalThis, "localStorage", {
    value: {
      setItem: (key: string) => storageWrites.push(key),
      getItem: () => null,
      removeItem: () => undefined,
    },
    configurable: true,
  });
  const client = new ApiClient("https://api.example.test", fetcher);

  const registered = await client.registerAndLogin({
    email: user.email,
    password: "form-only-password",
    first_name: user.first_name,
    last_name: user.last_name,
  });

  assert.equal(registered.id, user.id);
  assert.deepEqual(
    requests.map((request) => new URL(request.url).pathname),
    ["/api/v1/auth/register", "/api/v1/auth/login"],
  );
  assert.ok(
    requests.every((request) => request.init?.credentials === "include"),
  );
  assert.deepEqual(storageWrites, []);
});

test("logout during registration prevents the in-memory credentials from continuing to login", async () => {
  const registrationStarted = deferred<void>();
  const registrationResponse = deferred<Response>();
  let loginCalls = 0;
  let logoutCalls = 0;
  const client = new ApiClient("https://api.example.test", async (input) => {
    const path = new URL(String(input)).pathname;
    if (path.endsWith("/register")) {
      registrationStarted.resolve(undefined);
      return registrationResponse.promise;
    }
    if (path.endsWith("/login")) {
      loginCalls += 1;
      return jsonResponse(session("registration-login-must-not-run"));
    }
    logoutCalls += 1;
    return new Response(null, { status: 204 });
  });

  const registration = client.registerAndLogin({
    email: user.email,
    password: "form-only-password",
    first_name: user.first_name,
    last_name: user.last_name,
  });
  const registrationOutcome = registration.then(
    () => null,
    (error: unknown) => error,
  );
  await registrationStarted.promise;
  assert.equal(await client.logout(), true);
  registrationResponse.resolve(jsonResponse(user, 201));

  const registrationError = await registrationOutcome;
  assert.equal(
    (registrationError as Error).name,
    "AuthLifecycleCancelledError",
  );
  assert.equal(logoutCalls, 1);
  assert.equal(loginCalls, 0);
  assert.deepEqual(client.getSessionSnapshot(), {
    user: null,
    authenticated: false,
  });
});

test("login and bootstrap credentials remain memory-only", async () => {
  const requests: RequestInit[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    requests.push(init ?? {});
    return jsonResponse(
      String(input).endsWith("/refresh")
        ? session("memory-session-restored")
        : session("memory-session-login"),
    );
  };
  const client = new ApiClient("https://api.example.test", fetcher);

  await client.login({ email: user.email, password: "form-only-password" });
  assert.equal(client.getSessionSnapshot().authenticated, true);
  client.clearSession();
  const restored = await client.bootstrap();

  assert.equal(restored?.id, user.id);
  assert.ok(requests.every((request) => request.credentials === "include"));
  assert.equal(client.getSessionSnapshot().authenticated, true);
});

test("failed bootstrap resolves into a clean logged-out state", async () => {
  const client = new ApiClient("https://api.example.test", async () =>
    jsonResponse({ detail: "Invalid session" }, 401),
  );

  const restored = await client.bootstrap();

  assert.equal(restored, null);
  assert.deepEqual(client.getSessionSnapshot(), {
    user: null,
    authenticated: false,
  });
});

test("concurrent 401 responses share one rotating refresh request", async () => {
  let refreshCalls = 0;
  const refreshStarted = deferred<void>();
  const refreshResponse = deferred<Response>();
  const authorizedHeaders: string[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    const path = new URL(String(input)).pathname;
    const authorization = new Headers(init?.headers).get("authorization");
    if (path.endsWith("/login")) {
      return jsonResponse(session("memory-session-old"));
    }
    if (path.endsWith("/refresh")) {
      refreshCalls += 1;
      refreshStarted.resolve(undefined);
      return refreshResponse.promise;
    }
    if (authorization) authorizedHeaders.push(authorization);
    if (authorization?.endsWith("memory-session-old")) {
      return jsonResponse({ detail: "Expired" }, 401);
    }
    return jsonResponse({ ok: true });
  };
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only-password" });

  const pending = Promise.all([
    client.request<{ ok: boolean }>("/api/v1/one"),
    client.request<{ ok: boolean }>("/api/v1/two"),
    client.request<{ ok: boolean }>("/api/v1/three"),
  ]);
  await refreshStarted.promise;
  assert.equal(refreshCalls, 1);
  refreshResponse.resolve(jsonResponse(session("memory-session-new")));
  const results = await pending;

  assert.ok(results.every((result) => result.ok));
  assert.equal(refreshCalls, 1);
  assert.equal(
    authorizedHeaders.filter((value) => value.endsWith("memory-session-new"))
      .length,
    3,
  );
});

test("login waits for an older refresh so login is the final cookie-mutating request", async () => {
  const refreshStarted = deferred<void>();
  const refreshResponse = deferred<Response>();
  const events: string[] = [];
  const snapshots: Array<{ authenticated: boolean; user: UserPublic | null }> =
    [];
  let initialLoginComplete = false;
  let explicitLoginCalls = 0;
  const fetcher: typeof fetch = async (input, init) => {
    const path = new URL(String(input)).pathname;
    if (path.endsWith("/refresh")) {
      events.push("refresh-started");
      refreshStarted.resolve(undefined);
      return refreshResponse.promise.then((response) => {
        events.push("refresh-settled");
        return response;
      });
    }
    if (path.endsWith("/login")) {
      const body = JSON.parse(String(init?.body)) as UserLoginInput;
      if (!initialLoginComplete) {
        initialLoginComplete = true;
        return jsonResponse(session("memory-session-old"));
      }
      explicitLoginCalls += 1;
      events.push(`login:${body.email}`);
      return jsonResponse(sessionFor(secondUser, "memory-session-login-new"));
    }
    return jsonResponse({ ok: true });
  };
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only-password" });
  client.subscribe((snapshot) => snapshots.push(snapshot));

  const refreshRequest = client.refreshSession();
  const refreshOutcome = refreshRequest.then(
    () => null,
    (error: unknown) => error,
  );
  await refreshStarted.promise;
  const explicitLoginSnapshotStart = snapshots.length;
  const loginRequest = client.login({
    email: secondUser.email,
    password: "second-form-only-password",
  });

  await Promise.resolve();
  assert.equal(explicitLoginCalls, 0);
  refreshResponse.resolve(
    jsonResponse(session("memory-session-refreshed-old")),
  );
  const loggedInUser = await loginRequest;
  const refreshError = await refreshOutcome;

  assert.equal((refreshError as Error).name, "AuthLifecycleCancelledError");
  assert.equal(loggedInUser.id, secondUser.id);
  assert.deepEqual(events, [
    "refresh-started",
    "refresh-settled",
    `login:${secondUser.email}`,
  ]);
  assert.deepEqual(client.getSessionSnapshot(), {
    user: secondUser,
    authenticated: true,
  });
  assert.ok(
    snapshots
      .slice(explicitLoginSnapshotStart)
      .every((snapshot) => snapshot.user?.id !== user.id),
  );
});

test("logout waits for a running login and remains the final cookie-mutating request", async () => {
  const loginStarted = deferred<void>();
  const loginResponse = deferred<Response>();
  const events: string[] = [];
  const snapshots: Array<{ authenticated: boolean; user: UserPublic | null }> =
    [];
  let logoutCalls = 0;
  const client = new ApiClient(
    "https://api.example.test",
    async (input, init) => {
      const path = new URL(String(input)).pathname;
      if (path.endsWith("/login")) {
        events.push("login-started");
        loginStarted.resolve(undefined);
        return loginResponse.promise.then((response) => {
          events.push("login-settled");
          return response;
        });
      }
      if (path.endsWith("/logout")) {
        events.push("logout");
        logoutCalls += 1;
        assert.equal(init?.credentials, "include");
        return new Response(null, { status: 204 });
      }
      return jsonResponse({ ok: true });
    },
  );
  client.subscribe((snapshot) => snapshots.push(snapshot));

  const loginRequest = client.login({
    email: secondUser.email,
    password: "second-form-only-password",
  });
  const loginOutcome = loginRequest.then(
    () => null,
    (error: unknown) => error,
  );
  await loginStarted.promise;
  const logoutSnapshotStart = snapshots.length;
  const logoutRequest = client.logout();

  assert.deepEqual(client.getSessionSnapshot(), {
    user: null,
    authenticated: false,
  });
  assert.equal(logoutCalls, 0);

  loginResponse.resolve(
    jsonResponse(sessionFor(secondUser, "late-login-session")),
  );
  assert.equal(await logoutRequest, true);
  const loginError = await loginOutcome;

  assert.equal((loginError as Error).name, "AuthLifecycleCancelledError");
  assert.deepEqual(events, ["login-started", "login-settled", "logout"]);
  assert.equal(logoutCalls, 1);
  assert.deepEqual(client.getSessionSnapshot(), {
    user: null,
    authenticated: false,
  });
  assert.ok(
    snapshots
      .slice(logoutSnapshotStart)
      .every((snapshot) => !snapshot.authenticated && snapshot.user === null),
  );
});

test("concurrent login submissions execute serially and the final login owns state", async () => {
  const firstLoginStarted = deferred<void>();
  const firstLoginResponse = deferred<Response>();
  const secondLoginStarted = deferred<void>();
  const secondLoginResponse = deferred<Response>();
  const events: string[] = [];
  const client = new ApiClient(
    "https://api.example.test",
    async (_input, init) => {
      const body = JSON.parse(String(init?.body)) as UserLoginInput;
      if (body.email === user.email) {
        events.push("first-started");
        firstLoginStarted.resolve(undefined);
        return firstLoginResponse.promise.then((response) => {
          events.push("first-settled");
          return response;
        });
      }
      events.push("second-started");
      secondLoginStarted.resolve(undefined);
      return secondLoginResponse.promise.then((response) => {
        events.push("second-settled");
        return response;
      });
    },
  );

  const firstLogin = client.login({
    email: user.email,
    password: "first-form-only-password",
  });
  const secondLogin = client.login({
    email: secondUser.email,
    password: "second-form-only-password",
  });
  await firstLoginStarted.promise;
  assert.deepEqual(events, ["first-started"]);

  firstLoginResponse.resolve(jsonResponse(session("first-login-session")));
  await secondLoginStarted.promise;
  assert.deepEqual(events, [
    "first-started",
    "first-settled",
    "second-started",
  ]);
  secondLoginResponse.resolve(
    jsonResponse(sessionFor(secondUser, "second-login-session")),
  );
  const [firstResult, secondResult] = await Promise.all([
    firstLogin,
    secondLogin,
  ]);

  assert.equal(firstResult.id, user.id);
  assert.equal(secondResult.id, secondUser.id);
  assert.deepEqual(events, [
    "first-started",
    "first-settled",
    "second-started",
    "second-settled",
  ]);
  assert.deepEqual(client.getSessionSnapshot(), {
    user: secondUser,
    authenticated: true,
  });
});

test("a failed queued login does not prevent the next explicit login", async () => {
  const firstLoginStarted = deferred<void>();
  const firstLoginResponse = deferred<Response>();
  let secondLoginCalls = 0;
  const client = new ApiClient(
    "https://api.example.test",
    async (_input, init) => {
      const body = JSON.parse(String(init?.body)) as UserLoginInput;
      if (body.email === user.email) {
        firstLoginStarted.resolve(undefined);
        return firstLoginResponse.promise;
      }
      secondLoginCalls += 1;
      return jsonResponse(sessionFor(secondUser, "second-login-session"));
    },
  );

  const firstLogin = client.login({
    email: user.email,
    password: "wrong-form-only-password",
  });
  const firstOutcome = firstLogin.then(
    () => null,
    (error: unknown) => error,
  );
  const secondLogin = client.login({
    email: secondUser.email,
    password: "second-form-only-password",
  });
  await firstLoginStarted.promise;
  assert.equal(secondLoginCalls, 0);

  firstLoginResponse.resolve(
    jsonResponse({ detail: "Invalid credentials" }, 401),
  );
  const secondResult = await secondLogin;
  const firstError = await firstOutcome;

  assert.ok(firstError instanceof ApiError);
  assert.equal((firstError as ApiError).status, 401);
  assert.equal(secondResult.id, secondUser.id);
  assert.equal(secondLoginCalls, 1);
  assert.deepEqual(client.getSessionSnapshot(), {
    user: secondUser,
    authenticated: true,
  });
});

test("logout coordinates an in-flight refresh and stale completion cannot restore auth", async () => {
  const refreshStarted = deferred<void>();
  const refreshResponse = deferred<Response>();
  const events: string[] = [];
  const snapshots: Array<{ authenticated: boolean; user: UserPublic | null }> =
    [];
  let logoutCalls = 0;
  const fetcher: typeof fetch = async (input, init) => {
    const path = new URL(String(input)).pathname;
    const authorization = new Headers(init?.headers).get("authorization");
    if (path.endsWith("/login")) {
      return jsonResponse(session("memory-session-old"));
    }
    if (path.endsWith("/refresh")) {
      events.push("refresh");
      refreshStarted.resolve(undefined);
      return refreshResponse.promise;
    }
    if (path.endsWith("/logout")) {
      events.push("logout");
      logoutCalls += 1;
      assert.equal(init?.credentials, "include");
      return new Response(null, { status: 204 });
    }
    if (authorization?.endsWith("memory-session-old")) {
      return jsonResponse({ detail: "Expired" }, 401);
    }
    return jsonResponse({ ok: true });
  };
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only-password" });
  client.subscribe((snapshot) => snapshots.push(snapshot));

  const protectedRequest = client.request<{ ok: boolean }>("/api/v1/protected");
  const protectedOutcome = protectedRequest.then(
    () => null,
    (error: unknown) => error,
  );
  await refreshStarted.promise;
  const logoutSnapshotStart = snapshots.length;
  const logoutRequest = client.logout();

  assert.deepEqual(client.getSessionSnapshot(), {
    user: null,
    authenticated: false,
  });
  assert.equal(logoutCalls, 0);

  refreshResponse.resolve(jsonResponse(session("memory-session-new")));
  assert.equal(await logoutRequest, true);
  const protectedError = await protectedOutcome;

  assert.equal((protectedError as Error).name, "AuthLifecycleCancelledError");
  assert.deepEqual(events, ["refresh", "logout"]);
  assert.equal(logoutCalls, 1);
  assert.deepEqual(client.getSessionSnapshot(), {
    user: null,
    authenticated: false,
  });
  assert.ok(
    snapshots
      .slice(logoutSnapshotStart)
      .every((snapshot) => !snapshot.authenticated && snapshot.user === null),
  );
});

test("logout blocks new refreshes during and after a failed backend logout", async () => {
  const logoutStarted = deferred<void>();
  const logoutResponse = deferred<Response>();
  let refreshCalls = 0;
  const fetcher: typeof fetch = async (input, init) => {
    const path = new URL(String(input)).pathname;
    if (path.endsWith("/login")) {
      return jsonResponse(session("memory-session-current"));
    }
    if (path.endsWith("/refresh")) {
      refreshCalls += 1;
      return jsonResponse(session("memory-session-should-not-exist"));
    }
    if (path.endsWith("/logout")) {
      logoutStarted.resolve(undefined);
      return logoutResponse.promise;
    }
    return jsonResponse({ detail: "Unauthenticated" }, 401);
  };
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only-password" });

  const logoutRequest = client.logout();
  await logoutStarted.promise;
  await assert.rejects(
    client.request("/api/v1/during-logout"),
    (error: unknown) => error instanceof ApiError && error.status === 401,
  );
  assert.equal(refreshCalls, 0);

  logoutResponse.resolve(
    jsonResponse({ detail: "Temporarily unavailable" }, 503),
  );
  assert.equal(await logoutRequest, false);
  await assert.rejects(
    client.request("/api/v1/after-logout"),
    (error: unknown) => error instanceof ApiError && error.status === 401,
  );

  assert.equal(refreshCalls, 0);
  assert.deepEqual(client.getSessionSnapshot(), {
    user: null,
    authenticated: false,
  });
});

test("logout blocks login and refresh until a later successful explicit login", async () => {
  const logoutStarted = deferred<void>();
  const logoutResponse = deferred<Response>();
  let loginCalls = 0;
  let refreshCalls = 0;
  const fetcher: typeof fetch = async (input, init) => {
    const path = new URL(String(input)).pathname;
    if (path.endsWith("/login")) {
      loginCalls += 1;
      const body = JSON.parse(String(init?.body)) as UserLoginInput;
      if (body.email === secondUser.email) {
        return jsonResponse({ detail: "Invalid credentials" }, 401);
      }
      return jsonResponse(session(`memory-session-${loginCalls}`));
    }
    if (path.endsWith("/refresh")) {
      refreshCalls += 1;
      return jsonResponse(session("memory-session-refreshed"));
    }
    if (path.endsWith("/logout")) {
      logoutStarted.resolve(undefined);
      return logoutResponse.promise;
    }
    const authorization = new Headers(init?.headers).get("authorization");
    if (authorization?.endsWith("memory-session-3")) {
      return jsonResponse({ detail: "Expired" }, 401);
    }
    return jsonResponse({ ok: true });
  };
  const client = new ApiClient("https://api.example.test", fetcher);

  await client.login({ email: user.email, password: "form-only-password" });
  const logoutRequest = client.logout();
  await logoutStarted.promise;
  await assert.rejects(
    client.login({ email: secondUser.email, password: "blocked-password" }),
    (error: unknown) => (error as Error).name === "AuthLifecycleCancelledError",
  );
  await assert.rejects(
    client.refreshSession(),
    (error: unknown) => (error as Error).name === "AuthLifecycleCancelledError",
  );
  assert.equal(loginCalls, 1);
  assert.equal(refreshCalls, 0);

  logoutResponse.resolve(new Response(null, { status: 204 }));
  assert.equal(await logoutRequest, true);
  await assert.rejects(
    client.refreshSession(),
    (error: unknown) => (error as Error).name === "AuthLifecycleCancelledError",
  );
  assert.equal(refreshCalls, 0);

  await assert.rejects(
    client.login({
      email: secondUser.email,
      password: "failed-form-only-password",
    }),
    (error: unknown) => error instanceof ApiError && error.status === 401,
  );
  await assert.rejects(
    client.refreshSession(),
    (error: unknown) => (error as Error).name === "AuthLifecycleCancelledError",
  );
  assert.equal(refreshCalls, 0);

  await client.login({ email: user.email, password: "form-only-password" });
  assert.equal(
    (await client.request<{ ok: boolean }>("/api/v1/protected")).ok,
    true,
  );

  assert.equal(loginCalls, 3);
  assert.equal(refreshCalls, 1);
  assert.equal(client.getSessionSnapshot().authenticated, true);
  assert.equal(client.getSessionSnapshot().user?.id, user.id);
});

test("a protected request retries once and never creates a refresh loop", async () => {
  let protectedCalls = 0;
  let refreshCalls = 0;
  const fetcher: typeof fetch = async (input) => {
    const path = new URL(String(input)).pathname;
    if (path.endsWith("/login")) {
      return jsonResponse(session("memory-session-old"));
    }
    if (path.endsWith("/refresh")) {
      refreshCalls += 1;
      return jsonResponse(session("memory-session-new"));
    }
    protectedCalls += 1;
    return jsonResponse({ detail: "Still unauthorized" }, 401);
  };
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only-password" });

  await assert.rejects(
    client.request("/api/v1/protected"),
    (error: unknown) => error instanceof ApiError && error.status === 401,
  );
  assert.equal(protectedCalls, 2);
  assert.equal(refreshCalls, 1);
});

test("refresh failure logs out every waiting request", async () => {
  const fetcher: typeof fetch = async (input) => {
    const path = new URL(String(input)).pathname;
    if (path.endsWith("/login")) {
      return jsonResponse(session("memory-session-old"));
    }
    return jsonResponse({ detail: "Invalid session" }, 401);
  };
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only-password" });

  await assert.rejects(client.request("/api/v1/protected"));

  assert.equal(client.getSessionSnapshot().authenticated, false);
  assert.equal(client.getSessionSnapshot().user, null);
});

test("auth lifecycle failures never recursively invoke refresh", async () => {
  const paths: string[] = [];
  const client = new ApiClient("https://api.example.test", async (input) => {
    paths.push(new URL(String(input)).pathname);
    return jsonResponse({ detail: "Invalid credentials" }, 401);
  });

  await assert.rejects(
    client.login({ email: user.email, password: "form-only-password" }),
  );

  assert.deepEqual(paths, ["/api/v1/auth/login"]);
});

test("AbortSignal cancellation remains cancellation instead of a network error", async () => {
  const controller = new AbortController();
  const requestStarted = deferred<void>();
  const abortError = new DOMException("The request was aborted.", "AbortError");
  const client = new ApiClient(
    "https://api.example.test",
    async (_input, init) =>
      new Promise<Response>((_resolve, reject) => {
        requestStarted.resolve(undefined);
        init?.signal?.addEventListener("abort", () => reject(abortError), {
          once: true,
        });
      }),
  );

  const request = client.request("/api/v1/cancellable", {
    signal: controller.signal,
  });
  await requestStarted.promise;
  controller.abort();

  await assert.rejects(request, (error: unknown) => {
    assert.equal(error, abortError);
    assert.equal(error instanceof ApiNetworkError, false);
    return true;
  });
});

test("AbortError is preserved even without an attached AbortSignal", async () => {
  const abortError = new DOMException("Operation cancelled.", "AbortError");
  const client = new ApiClient("https://api.example.test", async () => {
    throw abortError;
  });

  await assert.rejects(
    client.request("/api/v1/cancelled"),
    (error: unknown) => {
      assert.equal(error, abortError);
      assert.equal(error instanceof ApiNetworkError, false);
      return true;
    },
  );
});

test("an aborted supplied signal preserves the original fetch rejection", async () => {
  const controller = new AbortController();
  const cancellation = new Error("Fetcher stopped after cancellation.");
  const client = new ApiClient("https://api.example.test", async () => {
    controller.abort();
    throw cancellation;
  });

  await assert.rejects(
    client.request("/api/v1/cancelled", { signal: controller.signal }),
    (error: unknown) => error === cancellation,
  );
});

test("genuine fetch rejection remains ApiNetworkError", async () => {
  const client = new ApiClient("https://api.example.test", async () => {
    throw new TypeError("Connection refused");
  });

  await assert.rejects(
    client.request("/api/v1/unreachable"),
    (error: unknown) => error instanceof ApiNetworkError,
  );
});

test("204 responses still parse as null", async () => {
  const client = new ApiClient("https://api.example.test", async (input) =>
    String(input).endsWith("/login")
      ? jsonResponse(session("memory-session-204"))
      : new Response(null, { status: 204 }),
  );
  await client.login({ email: user.email, password: "form-only-password" });

  assert.equal(
    await client.request<null>("/api/v1/empty", { method: "DELETE" }),
    null,
  );
});

test("me uses the in-memory bearer and logout clears state even on 503", async () => {
  const authorizations: Array<string | null> = [];
  const logoutRequests: RequestInit[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    const path = new URL(String(input)).pathname;
    if (path.endsWith("/login")) {
      return jsonResponse(session("memory-session-current"));
    }
    if (path.endsWith("/me")) {
      authorizations.push(new Headers(init?.headers).get("authorization"));
      return jsonResponse(user);
    }
    logoutRequests.push(init ?? {});
    return jsonResponse({ detail: "Temporarily unavailable" }, 503);
  };
  const client = new ApiClient("https://api.example.test", fetcher);
  await client.login({ email: user.email, password: "form-only-password" });

  await client.getCurrentUser();
  const loggedOutOnServer = await client.logout();

  assert.equal(authorizations.length, 1);
  assert.ok(authorizations[0]?.startsWith("Bearer "));
  assert.equal(logoutRequests.length, 1);
  assert.equal(logoutRequests[0].credentials, "include");
  assert.equal(
    new Headers(logoutRequests[0].headers).has("authorization"),
    false,
  );
  assert.equal(loggedOutOnServer, false);
  assert.equal(client.getSessionSnapshot().authenticated, false);
});

test("successful business responses are accepted for create and idempotent retry statuses", async () => {
  for (const status of [201, 200]) {
    const client = new ApiClient("https://api.example.test", async (input) => {
      const path = new URL(String(input)).pathname;
      if (path.endsWith("/login")) {
        return jsonResponse(session("memory-session-business"));
      }
      return jsonResponse(
        {
          business: { id: "business-id" },
          branding: null,
          created: status === 201,
        },
        status,
      );
    });
    await client.login({ email: user.email, password: "form-only-password" });

    const response = await client.request<{ created: boolean }>(
      "/api/v1/businesses",
      { method: "POST", json: { business_id: "business-id" } },
    );

    assert.equal(response.created, status === 201);
  }
});

test("branding API uses authenticated GET and PUT with source colors only", async () => {
  const requests: Array<{ path: string; init?: RequestInit }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    const path = new URL(String(input)).pathname;
    requests.push({ path, init });
    if (path.endsWith("/login")) {
      return jsonResponse(session("memory-session-branding"));
    }
    return jsonResponse({
      primary_color: "#123456",
      secondary_color: null,
      accent_color: null,
      logo_url: null,
    });
  };
  const client = new ApiClient("https://api.example.test", fetcher);
  const businesses = createBusinessApi(client);
  const businessId = "20000000-0000-4000-8000-000000000001";
  await client.login({ email: user.email, password: "form-only-password" });

  await businesses.getBranding(businessId);
  await businesses.updateBranding(businessId, {
    primary_color: "#123456",
    secondary_color: null,
    accent_color: null,
  });

  const brandingRequests = requests.slice(1);
  assert.deepEqual(
    brandingRequests.map(({ path }) => path),
    [
      `/api/v1/businesses/${businessId}/branding`,
      `/api/v1/businesses/${businessId}/branding`,
    ],
  );
  assert.deepEqual(
    brandingRequests.map(({ init }) => init?.method),
    ["GET", "PUT"],
  );
  assert.ok(
    brandingRequests.every(({ init }) =>
      new Headers(init?.headers).get("authorization")?.startsWith("Bearer "),
    ),
  );
  assert.equal(String(brandingRequests[1].init?.body).includes("logo"), false);
});

test("logo upload uses authenticated multipart and delete uses the tenant path", async () => {
  const requests: Array<{ path: string; init?: RequestInit }> = [];
  const fetcher: typeof fetch = async (input, init) => {
    const path = new URL(String(input)).pathname;
    requests.push({ path, init });
    if (path.endsWith("/login")) {
      return jsonResponse(session("memory-session-logo"));
    }
    if (init?.method === "DELETE") return new Response(null, { status: 204 });
    return jsonResponse({
      primary_color: "#123456",
      secondary_color: null,
      accent_color: null,
      logo_url: "/api/v1/media/businesses/current/logo.png",
    });
  };
  const client = new ApiClient("https://api.example.test", fetcher);
  const businesses = createBusinessApi(client);
  const businessId = "20000000-0000-4000-8000-000000000001";
  const logo = new File([new Uint8Array([1, 2, 3])], "wide-logo.png", {
    type: "image/png",
  });
  await client.login({ email: user.email, password: "form-only-password" });

  await businesses.uploadLogo(businessId, logo);
  const deletionResult = await businesses.deleteLogo(businessId);

  const [upload, deletion] = requests.slice(1);
  const expectedPath = `/api/v1/businesses/${businessId}/branding/logo`;
  assert.equal(upload.path, expectedPath);
  assert.equal(upload.init?.method, "POST");
  assert.ok(upload.init?.body instanceof FormData);
  const uploadedFile = (upload.init?.body as FormData).get("file");
  assert.ok(uploadedFile instanceof File);
  assert.equal(uploadedFile.name, logo.name);
  assert.equal(uploadedFile.type, logo.type);
  assert.equal(uploadedFile.size, logo.size);
  assert.equal(new Headers(upload.init?.headers).has("content-type"), false);
  assert.match(
    new Headers(upload.init?.headers).get("authorization") ?? "",
    /^Bearer /,
  );
  assert.equal(deletion.path, expectedPath);
  assert.equal(deletion.init?.method, "DELETE");
  assert.equal(deletionResult, null);
});

test("auth source contains no JavaScript credential persistence or prototype fallback", async () => {
  const [clientSource, contextSource, pagesSource] = await Promise.all([
    readFile(new URL("../services/api-client.ts", import.meta.url), "utf8"),
    readFile(
      new URL("../features/auth/auth-context.tsx", import.meta.url),
      "utf8",
    ),
    readFile(
      new URL("../features/auth/auth-pages.tsx", import.meta.url),
      "utf8",
    ),
  ]);
  const authSource = `${clientSource}\n${contextSource}\n${pagesSource}`;

  assert.equal(authSource.includes("ai-business-os:prototype-session"), false);
  assert.equal(clientSource.includes("localStorage"), false);
  assert.equal(clientSource.includes("sessionStorage"), false);
  assert.equal(clientSource.includes("document.cookie"), false);
  assert.equal(clientSource.includes("aibos_refresh"), false);
  assert.doesNotMatch(
    pagesSource,
    /(?:local|session)Storage\.setItem\([^)]*password/i,
  );
});
