import { API_BASE_URL } from "../config/api.ts";
import type {
  ApiErrorPayload,
  BusinessOnboardingInput,
  BusinessOnboardingResponse,
  BusinessProfileUpdate,
  BusinessBrandingResponse,
  BusinessBrandingUpdate,
  BusinessSummary,
  UserLoginInput,
  UserLoginResponse,
  UserPublic,
  UserRegistrationInput,
} from "./api-types.ts";

type Fetcher = typeof fetch;
type SessionListener = (snapshot: AuthSessionSnapshot) => void;

type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  json?: unknown;
  body?: BodyInit;
  headers?: HeadersInit;
  signal?: AbortSignal;
};

export type AuthSessionSnapshot = {
  user: UserPublic | null;
  authenticated: boolean;
};

const AUTH_LIFECYCLE_PATHS = new Set([
  "/api/v1/auth/login",
  "/api/v1/auth/register",
  "/api/v1/auth/refresh",
  "/api/v1/auth/logout",
]);

export class ApiError extends Error {
  readonly status: number;
  readonly data: ApiErrorPayload | null;

  constructor(status: number, data: ApiErrorPayload | null) {
    super(`Request failed with status ${status}.`);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

export class ApiNetworkError extends Error {
  constructor() {
    super("The service could not be reached.");
    this.name = "ApiNetworkError";
  }
}

class AuthLifecycleCancelledError extends Error {
  constructor() {
    super("The authentication lifecycle changed before the request completed.");
    this.name = "AuthLifecycleCancelledError";
  }
}

export class ApiClient {
  readonly #baseUrl: string;
  readonly #fetcher: Fetcher;
  readonly #listeners = new Set<SessionListener>();

  #accessToken: string | null = null;
  #user: UserPublic | null = null;

  #refreshPromise: Promise<UserLoginResponse> | null = null;
  #loginQueueTail: Promise<void> = Promise.resolve();
  #pendingLoginCount = 0;
  #logoutPromise: Promise<boolean> | null = null;

  #authGeneration = 0;
  #logoutGeneration = 0;

  #automaticRefreshBlocked = false;
  #logoutInProgress = false;

  constructor(baseUrl = API_BASE_URL, fetcher: Fetcher = fetch) {
    this.#baseUrl = baseUrl.replace(/\/+$/, "");
    this.#fetcher = fetcher;
  }

  getSessionSnapshot(): AuthSessionSnapshot {
    return {
      user: this.#user,
      authenticated: Boolean(this.#accessToken && this.#user),
    };
  }

  subscribe(listener: SessionListener) {
    this.#listeners.add(listener);

    listener(this.getSessionSnapshot());

    return () => {
      this.#listeners.delete(listener);
    };
  }

  async bootstrap(): Promise<UserPublic | null> {
    try {
      const session = await this.refreshSession();
      return session.user;
    } catch (error) {
      this.clearSession();
      if (
        error instanceof AuthLifecycleCancelledError ||
        (error instanceof ApiError && [401, 403].includes(error.status))
      ) {
        return null;
      }
      throw error;
    }
  }

  login(credentials: UserLoginInput): Promise<UserPublic> {
    if (this.#logoutInProgress) {
      return Promise.reject(new AuthLifecycleCancelledError());
    }

    const logoutGeneration = this.#logoutGeneration;

    this.#pendingLoginCount += 1;

    const loginOperation = this.#loginQueueTail.then(() =>
      this.#performLogin(credentials, logoutGeneration),
    );

    this.#loginQueueTail = loginOperation.then(
      () => undefined,
      () => undefined,
    );

    return loginOperation.finally(() => {
      this.#pendingLoginCount -= 1;
    });
  }

  async registerAndLogin(
    registration: UserRegistrationInput,
  ): Promise<UserPublic> {
    if (this.#logoutInProgress) {
      throw new AuthLifecycleCancelledError();
    }

    const logoutGeneration = this.#logoutGeneration;

    await this.#lifecycleRequest<UserPublic>(
      "/api/v1/auth/register",
      {
        method: "POST",
        json: registration,
      },
    );

    this.#assertLogoutGeneration(logoutGeneration);

    return this.login({
      email: registration.email,
      password: registration.password,
    });
  }

  logout(): Promise<boolean> {
    if (this.#logoutPromise) {
      return this.#logoutPromise;
    }

    this.#logoutInProgress = true;
    this.#automaticRefreshBlocked = true;

    this.#authGeneration += 1;
    this.#logoutGeneration += 1;

    this.clearSession();

    const runningRefresh = this.#refreshPromise;

    const runningLogins =
      this.#pendingLoginCount > 0
        ? this.#loginQueueTail
        : null;

    this.#logoutPromise = this.#performLogout(
      runningRefresh,
      runningLogins,
    ).finally(() => {
      if (this.#accessToken || this.#user) {
        this.clearSession();
      }

      this.#logoutInProgress = false;
      this.#logoutPromise = null;
    });

    return this.#logoutPromise;
  }

  async refreshSession(): Promise<UserLoginResponse> {
    if (
      this.#automaticRefreshBlocked ||
      this.#logoutInProgress ||
      this.#pendingLoginCount > 0
    ) {
      throw new AuthLifecycleCancelledError();
    }

    if (!this.#refreshPromise) {
      const generation = this.#authGeneration;

      let trackedRefresh: Promise<UserLoginResponse>;

      trackedRefresh = this.#performRefresh(generation).finally(() => {
        if (this.#refreshPromise === trackedRefresh) {
          this.#refreshPromise = null;
        }
      });

      this.#refreshPromise = trackedRefresh;
    }

    return this.#refreshPromise;
  }

  async getCurrentUser(): Promise<UserPublic> {
    const generation = this.#authGeneration;

    const user = await this.request<UserPublic>(
      "/api/v1/auth/me",
    );

    if (
      generation === this.#authGeneration &&
      !this.#automaticRefreshBlocked &&
      !this.#logoutInProgress
    ) {
      this.#user = user;
      this.#notify();
    }

    return user;
  }

  async request<T>(
    path: string,
    options: RequestOptions = {},
  ): Promise<T> {
    return this.#authenticatedRequest<T>(
      path,
      options,
      false,
      this.#authGeneration,
    );
  }

  clearSession(): void {
    this.#accessToken = null;
    this.#user = null;

    this.#notify();
  }

  async #authenticatedRequest<T>(
    path: string,
    options: RequestOptions,
    retried: boolean,
    requestGeneration: number,
  ): Promise<T> {
    const headers = new Headers(options.headers);

    if (this.#accessToken) {
      headers.set(
        "Authorization",
        `Bearer ${this.#accessToken}`,
      );
    }

    try {
      return await this.#request<T>(
        path,
        {
          ...options,
          headers,
        },
      );
    } catch (error) {
      if (
        error instanceof ApiError &&
        error.status === 401 &&
        !retried &&
        !AUTH_LIFECYCLE_PATHS.has(path) &&
        requestGeneration === this.#authGeneration &&
        !this.#automaticRefreshBlocked &&
        !this.#logoutInProgress
      ) {
        await this.refreshSession();

        if (
          requestGeneration !== this.#authGeneration ||
          this.#automaticRefreshBlocked ||
          this.#logoutInProgress
        ) {
          throw new AuthLifecycleCancelledError();
        }

        return this.#authenticatedRequest<T>(
          path,
          options,
          true,
          requestGeneration,
        );
      }

      throw error;
    }
  }

  async #performRefresh(
    generation: number,
  ): Promise<UserLoginResponse> {
    try {
      const session =
        await this.#lifecycleRequest<UserLoginResponse>(
          "/api/v1/auth/refresh",
          {
            method: "POST",
          },
        );

      if (
        generation !== this.#authGeneration ||
        this.#automaticRefreshBlocked ||
        this.#logoutInProgress
      ) {
        throw new AuthLifecycleCancelledError();
      }

      this.#setSession(session);

      return session;
    } catch (error) {
      if (generation === this.#authGeneration) {
        this.clearSession();
      }

      throw error;
    }
  }

  async #performLogin(
    credentials: UserLoginInput,
    logoutGeneration: number,
  ): Promise<UserPublic> {
    this.#assertLogoutGeneration(logoutGeneration);

    const runningRefresh = this.#refreshPromise;
    const generation = ++this.#authGeneration;

    if (runningRefresh) {
      try {
        await runningRefresh;
      } catch {
        // Login remains the newer cookie-mutating request after refresh settles.
      }
    }

    this.#assertLogoutGeneration(logoutGeneration);

    if (generation !== this.#authGeneration) {
      throw new AuthLifecycleCancelledError();
    }

    const session =
      await this.#lifecycleRequest<UserLoginResponse>(
        "/api/v1/auth/login",
        {
          method: "POST",
          json: credentials,
        },
      );

    this.#assertLogoutGeneration(logoutGeneration);

    if (generation !== this.#authGeneration) {
      throw new AuthLifecycleCancelledError();
    }

    this.#automaticRefreshBlocked = false;

    this.#setSession(session);

    return session.user;
  }

  async #performLogout(
    runningRefresh: Promise<UserLoginResponse> | null,
    runningLogins: Promise<void> | null,
  ): Promise<boolean> {
    const runningAuthWork: Promise<unknown>[] = [];

    if (runningRefresh) {
      runningAuthWork.push(runningRefresh);
    }

    if (runningLogins) {
      runningAuthWork.push(runningLogins);
    }

    await Promise.allSettled(runningAuthWork);

    try {
      await this.#lifecycleRequest<null>(
        "/api/v1/auth/logout",
        {
          method: "POST",
        },
      );

      return true;
    } catch {
      return false;
    }
  }

  #assertLogoutGeneration(
    logoutGeneration: number,
  ): void {
    if (
      this.#logoutInProgress ||
      logoutGeneration !== this.#logoutGeneration
    ) {
      throw new AuthLifecycleCancelledError();
    }
  }

  async #lifecycleRequest<T>(
    path: string,
    options: RequestOptions,
  ): Promise<T> {
    return this.#request<T>(
      path,
      options,
      "include",
    );
  }

  async #request<T>(
    path: string,
    options: RequestOptions,
    credentials?: RequestCredentials,
  ): Promise<T> {
    const headers = new Headers(options.headers);

    headers.set("Accept", "application/json");

    if (options.json !== undefined) {
      headers.set(
        "Content-Type",
        "application/json",
      );
    }

    let response: Response;

    try {
      /*
       * IMPORTANT:
       *
       * Do not call the stored native browser fetch as:
       *
       *   this.#fetcher(...)
       *
       * A native browser function stored as an object property can be
       * invoked with the wrong `this` context by some browser runtimes.
       *
       * Detaching it before invocation guarantees that native fetch is
       * executed as a standalone function.
       *
       * This also preserves injected fetch implementations used by tests.
       */
      const fetcher = this.#fetcher;

      response = await fetcher(
        `${this.#baseUrl}${path}`,
        {
          method: options.method ?? "GET",
          headers,
          credentials,
          signal: options.signal,
          body:
            options.json === undefined
              ? options.body
              : JSON.stringify(options.json),
        },
      );
    } catch (error) {
      if (
        isRequestCancellation(
          error,
          options.signal,
        )
      ) {
        throw error;
      }

      throw new ApiNetworkError();
    }

    const data = await parseResponse(response);

    if (!response.ok) {
      throw new ApiError(
        response.status,
        asErrorPayload(data),
      );
    }

    return data as T;
  }

  #setSession(
    session: UserLoginResponse,
  ): void {
    this.#accessToken = session.access_token;
    this.#user = session.user;

    this.#notify();
  }

  #notify(): void {
    const snapshot = this.getSessionSnapshot();

    this.#listeners.forEach((listener) => {
      listener(snapshot);
    });
  }
}

function isRequestCancellation(
  error: unknown,
  signal?: AbortSignal,
) {
  if (signal?.aborted) {
    return true;
  }

  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

async function parseResponse(
  response: Response,
): Promise<unknown> {
  if (response.status === 204) {
    return null;
  }

  const text = await response.text();

  if (!text.trim()) {
    return null;
  }

  const mediaType =
    response.headers
      .get("content-type")
      ?.toLowerCase();

  if (mediaType?.includes("json")) {
    try {
      return JSON.parse(text) as unknown;
    } catch {
      return null;
    }
  }

  return null;
}

function asErrorPayload(
  value: unknown,
): ApiErrorPayload | null {
  return value && typeof value === "object"
    ? (value as ApiErrorPayload)
    : null;
}

export function humanizeApiError(
  error: unknown,
  fallback: string,
): string {
  if (error instanceof ApiNetworkError) {
    return "Cannot reach AI Business OS API. Check the local API or your network connection.";
  }

  if (error instanceof ApiError) {
    const detail = error.data?.detail;

    if (
      typeof detail === "string" &&
      detail.trim()
    ) {
      return detail;
    }

    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      const safeMessage = typeof detail.message === "string"
        ? detail.message.trim()
        : "";
      const actionableErrors: Record<string, string> = {
        provider_not_configured: "Platform configuration required.",
        connection_required: "Connect the required provider account to continue.",
        authorization_expired: "The provider authorization expired. Reconnect the account.",
        permission_missing: "Your business role does not permit this action.",
        approval_required: "Human approval is required before this action can run.",
        spend_policy_required: "Configure an owner-managed advertising spend policy first.",
        spend_limit_exceeded: "This action exceeds the authorized advertising spend limit.",
        rate_limited: "Too many requests. Wait a moment and try again.",
        provider_unavailable: "The external provider is temporarily degraded. Your internal data remains available.",
        temporary_failure: "The service could not complete the request. Please try again.",
        external_outcome_uncertain: "The provider outcome is uncertain. This action will not be retried automatically.",
        feature_not_entitled: "Your current plan does not include this feature.",
        validation_error: "Check the supplied details and try again.",
      };
      const label = typeof detail.entitlement_key === "string"
        ? detail.entitlement_key.replace(/^max_/, "").replace(/_month$/, "").replaceAll("_", " ")
        : "plan access";
      if (
        detail.code === "usage_limit_reached" &&
        typeof detail.current === "number" &&
        typeof detail.limit === "number"
      ) {
        return `You've used ${detail.current.toLocaleString()} / ${detail.limit.toLocaleString()} ${label} this billing period. Review Billing to upgrade.`;
      }
      if (detail.code === "feature_not_in_plan") {
        return `Your current plan doesn't include ${label}. Review Billing to compare plans.`;
      }
      if (typeof detail.code === "string") {
        return safeMessage || actionableErrors[detail.code] || fallback;
      }
    }

    if (error.status === 422) {
      return "Check the highlighted details and try again.";
    }

    if (error.status === 503) {
      return "The service is temporarily unavailable. Please try again.";
    }
  }

  return fallback;
}

export const apiClient = new ApiClient();

export function createBusinessApi(
  client: ApiClient,
) {
  return {
    list: () =>
      client.request<BusinessSummary[]>(
        "/api/v1/businesses",
      ),

    create: (
      input: BusinessOnboardingInput,
    ) =>
      client.request<BusinessOnboardingResponse>(
        "/api/v1/businesses",
        {
          method: "POST",
          json: input,
        },
      ),

    updateProfile: (
      businessId: string,
      input: BusinessProfileUpdate,
    ) =>
      client.request<BusinessSummary>(
        `/api/v1/businesses/${encodeURIComponent(businessId)}`,
        {
          method: "PUT",
          json: input,
        },
      ),

    getBranding: (
      businessId: string,
    ) =>
      client.request<BusinessBrandingResponse>(
        `/api/v1/businesses/${encodeURIComponent(
          businessId,
        )}/branding`,
      ),

    updateBranding: (
      businessId: string,
      input: BusinessBrandingUpdate,
    ) =>
      client.request<BusinessBrandingResponse>(
        `/api/v1/businesses/${encodeURIComponent(
          businessId,
        )}/branding`,
        {
          method: "PUT",
          json: input,
        },
      ),

    uploadLogo: (
      businessId: string,
      file: File,
    ) => {
      const form = new FormData();

      form.append(
        "file",
        file,
        file.name,
      );

      return client.request<BusinessBrandingResponse>(
        `/api/v1/businesses/${encodeURIComponent(
          businessId,
        )}/branding/logo`,
        {
          method: "POST",
          body: form,
        },
      );
    },

    deleteLogo: (
      businessId: string,
    ) =>
      client.request<null>(
        `/api/v1/businesses/${encodeURIComponent(
          businessId,
        )}/branding/logo`,
        {
          method: "DELETE",
        },
      ),
  };
}

export const businessApi =
  createBusinessApi(apiClient);
