import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  getListBusinessesQueryKey,
  useCreateBusiness,
  useListBusinesses,
  useUpdateBusiness,
  type Business,
  type BusinessInput,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";

type BusinessContextValue = {
  businesses: Business[];
  activeBusiness?: Business;
  activeBusinessId: string;
  isLoading: boolean;
  selectBusiness: (id: string) => void;
  createBusiness: (input: BusinessInput) => Promise<Business>;
  updateBusiness: (id: string, input: BusinessInput) => Promise<Business>;
};

const BusinessContext = createContext<BusinessContextValue | null>(null);

function useStoredBusinessId() {
  const [value, setValue] = useState(() => {
    try {
      return localStorage.getItem("ai-os-active-business") ?? "green-valley-farms";
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
  const queryClient = useQueryClient();
  const { data, isLoading } = useListBusinesses();
  const [businesses, setBusinesses] = useState<Business[]>([]);
  const [activeBusinessId, setActiveBusinessId] = useStoredBusinessId();
  const createMutation = useCreateBusiness();
  const updateMutation = useUpdateBusiness();

  useEffect(() => {
    if (!data) return;
    setBusinesses(data);
    if (!data.some((business) => business.id === activeBusinessId) && data[0]) {
      setActiveBusinessId(data[0].id);
    }
  }, [activeBusinessId, data, setActiveBusinessId]);

  const activeBusiness = useMemo(
    () => businesses.find((business) => business.id === activeBusinessId) ?? businesses[0],
    [activeBusinessId, businesses],
  );

  const createBusiness = async (input: BusinessInput) => {
    const created = await createMutation.mutateAsync({ data: input });
    setBusinesses((current) => [...current, created]);
    setActiveBusinessId(created.id);
    await queryClient.invalidateQueries({ queryKey: getListBusinessesQueryKey() });
    return created;
  };

  const updateBusiness = async (id: string, input: BusinessInput) => {
    const updated = await updateMutation.mutateAsync({ id, data: input });
    setBusinesses((current) =>
      current.map((business) => (business.id === id ? updated : business)),
    );
    await queryClient.invalidateQueries({ queryKey: getListBusinessesQueryKey() });
    return updated;
  };

  return (
    <BusinessContext.Provider
      value={{
        businesses,
        activeBusiness,
        activeBusinessId,
        isLoading,
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
  if (!value) throw new Error("useBusiness must be used within BusinessProvider");
  return value;
}