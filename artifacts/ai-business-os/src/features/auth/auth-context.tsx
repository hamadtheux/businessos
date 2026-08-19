import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { authAdapter, type PrototypeUser } from "@/services/auth-adapter";

type AuthContextValue = {
  user: PrototypeUser | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<PrototypeUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    void authAdapter.getSession().then((session) => {
      setUser(session);
      setIsLoading(false);
    });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isLoading,
      login: async (email, password) => setUser(await authAdapter.login(email, password)),
      register: async (name, email, password) =>
        setUser(await authAdapter.register(name, email, password)),
      logout: async () => {
        await authAdapter.logout();
        setUser(null);
      },
    }),
    [user, isLoading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used within AuthProvider");
  return value;
}

