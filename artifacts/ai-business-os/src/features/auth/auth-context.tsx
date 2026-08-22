import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  apiClient,
  humanizeApiError,
} from "@/services/api-client";
import type { UserPublic } from "@/services/api-types";

export type AuthStatus =
  | "bootstrapping"
  | "authenticated"
  | "unauthenticated";

type AuthContextValue = {
  user: UserPublic | null;
  status: AuthStatus;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  reloadUser: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserPublic | null>(null);
  const [status, setStatus] = useState<AuthStatus>("bootstrapping");
  const bootstrapComplete = useRef(false);

  useEffect(() => {
    let active = true;
    const unsubscribe = apiClient.subscribe((snapshot) => {
      if (!active) return;
      setUser(snapshot.user);
      if (bootstrapComplete.current) {
        setStatus(
          snapshot.authenticated ? "authenticated" : "unauthenticated",
        );
      }
    });

    void apiClient.bootstrap().finally(() => {
      if (!active) return;
      bootstrapComplete.current = true;
      const snapshot = apiClient.getSessionSnapshot();
      setUser(snapshot.user);
      setStatus(
        snapshot.authenticated ? "authenticated" : "unauthenticated",
      );
    });

    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      status,
      isLoading: status === "bootstrapping",
      login: async (email, password) => {
        try {
          await apiClient.login({ email, password });
        } catch (error) {
          throw new Error(
            humanizeApiError(error, "We couldn't sign you in. Please try again."),
          );
        }
      },
      register: async (name, email, password) => {
        const [firstName, ...lastNameParts] = name.trim().split(/\s+/);
        try {
          await apiClient.registerAndLogin({
            email,
            password,
            first_name: firstName,
            last_name: lastNameParts.join(" ") || null,
          });
        } catch (error) {
          throw new Error(
            humanizeApiError(
              error,
              "We couldn't create your account. Please try again.",
            ),
          );
        }
      },
      logout: async () => {
        await apiClient.logout();
        setUser(null);
        setStatus("unauthenticated");
      },
      reloadUser: async () => {
        try {
          await apiClient.getCurrentUser();
        } catch (error) {
          throw new Error(
            humanizeApiError(
              error,
              "We couldn't refresh your account details.",
            ),
          );
        }
      },
    }),
    [user, status],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}

export function userDisplayName(user: UserPublic | null): string {
  if (!user) return "Account";
  return [user.first_name, user.last_name].filter(Boolean).join(" ");
}
