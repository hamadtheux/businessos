import type { AuthStatus } from "../features/auth/auth-context.tsx";

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
  const publicRoute = location === "/login" || location === "/register";
  if (
    isApplicationBootstrapping(status, businessesLoading) ||
    businessesError
  ) {
    return null;
  }
  if (status === "unauthenticated" && !publicRoute) return "/login";
  if (status !== "authenticated") return null;
  if (businessCount === 0 && location !== "/onboarding") {
    return "/onboarding";
  }
  if (businessCount > 0 && publicRoute) return "/dashboard";
  return null;
}
