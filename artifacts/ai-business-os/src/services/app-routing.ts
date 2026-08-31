import type { AuthStatus } from "../features/auth/auth-context.tsx";

const PUBLIC_ROUTES = new Set([
  "/",
  "/login",
  "/register",
  "/privacy",
  "/terms",
]);

const AUTH_ENTRY_ROUTES = new Set([
  "/login",
  "/register",
]);

function normalizeRoute(location: string): string {
  const [path] = location.split(/[?#]/, 1);
  return path || "/";
}

export function isPublicRoute(location: string): boolean {
  return PUBLIC_ROUTES.has(normalizeRoute(location));
}

export function isAuthEntryRoute(location: string): boolean {
  return AUTH_ENTRY_ROUTES.has(normalizeRoute(location));
}

export function isApplicationBootstrapping(
  status: AuthStatus,
  businessesLoading: boolean,
): boolean {
  return (
    status === "bootstrapping" ||
    (status === "authenticated" && businessesLoading)
  );
}

export function nextProtectedRoute({
  status,
  businessesLoading,
  businessesError,
  businessCount,
  location,
}: {
  status: AuthStatus;
  businessesLoading: boolean;
  businessesError: string;
  businessCount: number;
  location: string;
}): string | null {
  const publicRoute = isPublicRoute(location);
  const authEntryRoute = isAuthEntryRoute(location);

  if (
    isApplicationBootstrapping(status, businessesLoading) ||
    businessesError
  ) {
    return null;
  }

  if (status === "unauthenticated" && !publicRoute) {
    return "/login";
  }

  if (status !== "authenticated") {
    return null;
  }

  const normalizedLocation = normalizeRoute(location);
  const publicLegalRoute =
    normalizedLocation === "/privacy" || normalizedLocation === "/terms";

  if (
    businessCount === 0 &&
    normalizedLocation !== "/onboarding" &&
    !publicLegalRoute
  ) {
    return "/onboarding";
  }

  if (businessCount > 0 && authEntryRoute) {
    return "/dashboard";
  }

  return null;
}
