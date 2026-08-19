import {
  useCallback,
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { Business, BusinessInput } from "@workspace/api-client-react";
import { businessRepository } from "@/services/business-repository";

type BusinessContextValue = {
  businesses: Business[];
  activeBusiness?: Business;
  activeBusinessId: string;
  isLoading: boolean;
  selectBusiness: (id: string) => void;
  createBusiness: (
    input: BusinessInput,
    businessId?: string,
  ) => Promise<Business>;
  updateBusiness: (id: string, input: BusinessInput) => Promise<Business>;
};

const BusinessContext = createContext<BusinessContextValue | null>(null);

function useStoredBusinessId() {
  const [value, setValue] = useState(() => {
    try {
      return (
        localStorage.getItem("ai-os-active-business") ?? "green-valley-farms"
      );
    } catch {
      return "green-valley-farms";
    }
  });
  useEffect(() => {
    localStorage.setItem("ai-os-active-business", value);
  }, [value]);
  return [value, setValue] as const;
}

export function BusinessProvider({ children }: { children: ReactNode }) {
  const [businesses, setBusinesses] = useState<Business[]>(() =>
    businessRepository.list(),
  );
  const [activeBusinessId, setActiveBusinessId] = useStoredBusinessId();

  useEffect(() => {
    if (
      !businesses.some((business) => business.id === activeBusinessId) &&
      businesses[0]
    ) {
      setActiveBusinessId(businesses[0].id);
    }
  }, [activeBusinessId, businesses, setActiveBusinessId]);

  const activeBusiness = useMemo(
    () =>
      businesses.find((business) => business.id === activeBusinessId) ??
      businesses[0],
    [activeBusinessId, businesses],
  );

  const createBusiness = useCallback(
    async (input: BusinessInput, businessId?: string) => {
      const id =
        businessId ??
        `${input.name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${Date.now()}`;
      const created = businessRepository.upsert(id, input);
      setBusinesses((current) =>
        current.some((business) => business.id === created.id)
          ? current.map((business) =>
              business.id === created.id ? created : business,
            )
          : [...current, created],
      );
      return created;
    },
    [],
  );

  const updateBusiness = useCallback(
    async (id: string, input: BusinessInput) => {
      const updated = businessRepository.update(id, input);
      setBusinesses((current) =>
        current.map((business) => (business.id === id ? updated : business)),
      );
      return updated;
    },
    [],
  );

  return (
    <BusinessContext.Provider
      value={{
        businesses,
        activeBusiness,
        activeBusinessId,
        isLoading: false,
        selectBusiness: setActiveBusinessId,
        createBusiness,
        updateBusiness,
      }}
    >
      {children}
    </BusinessContext.Provider>
  );
}

export function useBusiness() {
  const value = useContext(BusinessContext);
  if (!value)
    throw new Error("useBusiness must be used within BusinessProvider");
  return value;
}
