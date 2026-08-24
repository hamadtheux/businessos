import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useAuth } from "@/features/auth/auth-context";
import { businessApi, humanizeApiError } from "@/services/api-client";
import { persistBusinessOnboarding } from "@/services/business-onboarding-persistence";
import {
  applyBrandingToBusinessList,
  brandingUpdateFromIdentity,
  brandingFromResponse,
  businessFromSummary,
  createBusinessProfilePayload,
  isCurrentBrandingResponse,
  resolveActiveBusinessId,
} from "@/services/business-model";
import type { BrandIdentity, Business, BusinessInput } from "@/types/business";
import {
  billingApi,
  isCurrentBillingResponse,
  type BillingOverview,
} from "@/services/billing";

const ACTIVE_BUSINESS_KEY = "ai-os-active-business";

type BusinessContextValue = {
  businesses: Business[];
  activeBusiness?: Business;
  activeBusinessId: string;
  isLoading: boolean;
  error: string;
  billing: BillingOverview | null;
  billingLoading: boolean;
  billingError: string;
  reloadBilling: () => Promise<BillingOverview | null>;
  selectBusiness: (id: string) => void;
  reloadBusinesses: () => Promise<Business[]>;
  createBusiness: (
    input: BusinessInput,
    businessId?: string,
  ) => Promise<Business>;
  updateBusiness: (id: string, input: BusinessInput) => Promise<Business>;
  updateBranding: (
    id: string,
    identity: BrandIdentity | null,
  ) => Promise<Business>;
  uploadLogo: (id: string, file: File) => Promise<Business>;
  deleteLogo: (id: string) => Promise<Business>;
};

const BusinessContext = createContext<BusinessContextValue | null>(null);

export function BusinessProvider({ children }: { children: ReactNode }) {
  const { status } = useAuth();
  const [businesses, setBusinesses] = useState<Business[]>([]);
  const [activeBusinessId, setActiveBusinessId] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [hasLoadedAuthenticatedSession, setHasLoadedAuthenticatedSession] =
    useState(false);
  const [error, setError] = useState("");
  const [billing, setBilling] = useState<BillingOverview | null>(null);
  const [billingLoading, setBillingLoading] = useState(false);
  const [billingError, setBillingError] = useState("");
  const billingVersion = useRef(0);
  const loadVersion = useRef(0);
  const brandingVersion = useRef(0);
  const businessesRef = useRef<Business[]>([]);
  const activeBusinessIdRef = useRef("");

  const clearBillingSnapshot = useCallback((loading = false) => {
    ++billingVersion.current;
    setBilling(null);
    setBillingError("");
    setBillingLoading(loading);
  }, []);

  const selectBusiness = useCallback((id: string) => {
    if (!businessesRef.current.some((business) => business.id === id)) {
      return;
    }
    const version = ++brandingVersion.current;
    const safeBusinesses = applyBrandingToBusinessList(
      businessesRef.current,
      id,
      null,
    );
    if (activeBusinessIdRef.current !== id) clearBillingSnapshot(true);
    businessesRef.current = safeBusinesses;
    activeBusinessIdRef.current = id;
    setBusinesses(safeBusinesses);
    setActiveBusinessId(id);
    setError("");
    writeStoredBusinessId(id);

    void businessApi
      .getBranding(id)
      .then((branding) => {
        if (
          !isCurrentBrandingResponse(
            id,
            version,
            activeBusinessIdRef.current,
            brandingVersion.current,
          )
        ) {
          return;
        }
        const next = applyBrandingToBusinessList(
          businessesRef.current,
          id,
          branding,
        );
        businessesRef.current = next;
        setBusinesses(next);
      })
      .catch(() => {
        // A tenant switch must not blank the authenticated application because
        // optional presentation data failed. Branding settings can retry it.
      });
  }, [clearBillingSnapshot]);

  const loadBusinesses = useCallback(
    async ({
      blockUi,
      surfaceError,
      preferredActiveId,
    }: {
      blockUi: boolean;
      surfaceError: boolean;
      preferredActiveId?: string;
    }) => {
      const version = ++loadVersion.current;
      const brandingRequestVersion = ++brandingVersion.current;
      if (blockUi) setIsLoading(true);
      if (surfaceError) setError("");
      try {
        const summaries = await businessApi.list();
        const loaded = summaries.map((summary) =>
          businessFromSummary(summary),
        );
        if (version !== loadVersion.current) return loaded;

        const nextActiveId = resolveActiveBusinessId(
          preferredActiveId ?? readStoredBusinessId(),
          loaded,
        );
        if (activeBusinessIdRef.current !== nextActiveId) {
          clearBillingSnapshot(Boolean(nextActiveId));
        }
        businessesRef.current = loaded;
        activeBusinessIdRef.current = nextActiveId;
        setBusinesses(loaded);
        setActiveBusinessId(nextActiveId);
        writeStoredBusinessId(nextActiveId);
        setError("");

        if (!nextActiveId) return loaded;

        try {
          const activeBranding = await businessApi.getBranding(nextActiveId);
          if (
            version !== loadVersion.current ||
            brandingRequestVersion !== brandingVersion.current
          ) {
            return loaded;
          }
          const branded = applyBrandingToBusinessList(
            loaded,
            nextActiveId,
            activeBranding,
          );
          businessesRef.current = branded;
          setBusinesses(branded);
          return branded;
        } catch {
          // Branding is optional presentation data. The authenticated business
          // list remains authoritative and usable if branding cannot refresh.
          return loaded;
        }
      } catch (reason) {
        if (version === loadVersion.current && surfaceError) {
          setBusinesses([]);
          businessesRef.current = [];
          setActiveBusinessId("");
          activeBusinessIdRef.current = "";
          setError(
            humanizeApiError(
              reason,
              "We couldn't load your businesses. Please try again.",
            ),
          );
        }
        throw reason;
      } finally {
        if (version === loadVersion.current) {
          if (blockUi) setIsLoading(false);
          setHasLoadedAuthenticatedSession(true);
        }
      }
    },
    [clearBillingSnapshot],
  );

  const reloadBusinesses = useCallback(
    () => loadBusinesses({ blockUi: true, surfaceError: true }),
    [loadBusinesses],
  );

  useEffect(() => {
    if (status === "authenticated") {
      void reloadBusinesses().catch(() => undefined);
      return;
    }

    ++loadVersion.current;
    ++brandingVersion.current;
    setHasLoadedAuthenticatedSession(false);
    setBusinesses([]);
    businessesRef.current = [];
    setActiveBusinessId("");
    activeBusinessIdRef.current = "";
    setError("");
    setIsLoading(status === "bootstrapping");
    if (status === "unauthenticated") writeStoredBusinessId("");
  }, [reloadBusinesses, status]);

  const activeBusiness = useMemo(
    () => businesses.find((business) => business.id === activeBusinessId),
    [activeBusinessId, businesses],
  );

  const reloadBilling = useCallback(async () => {
    const businessId = activeBusinessIdRef.current;
    if (!businessId || status !== "authenticated") {
      setBilling(null);
      return null;
    }
    const version = ++billingVersion.current;
    setBillingLoading(true);
    setBilling(null);
    setBillingError("");
    try {
      const value = await billingApi.overview(businessId);
      const isCurrent = isCurrentBillingResponse(
        businessId,
        value.business_id,
        version,
        billingVersion.current,
        activeBusinessIdRef.current,
      );
      if (isCurrent) {
        setBilling(value);
        return value;
      }
      if (version === billingVersion.current && activeBusinessIdRef.current === businessId) {
        setBilling(null);
        setBillingError("The billing response did not match the active business. Please try again.");
      }
      return null;
    } catch (reason) {
      if (version === billingVersion.current && activeBusinessIdRef.current === businessId) {
        setBilling(null);
        setBillingError(humanizeApiError(reason, "We couldn't load this business's plan access."));
      }
      return null;
    } finally {
      if (version === billingVersion.current) setBillingLoading(false);
    }
  }, [status]);

  useEffect(() => {
    if (status === "authenticated" && activeBusinessId) {
      void reloadBilling();
    } else {
      ++billingVersion.current;
      setBilling(null);
      setBillingError("");
      setBillingLoading(false);
    }
  }, [activeBusinessId, reloadBilling, status]);

  const createBusiness = useCallback(
    async (input: BusinessInput, businessId?: string) => {
      if (!businessId) {
        throw new Error("A stable business identity is required.");
      }
      await persistBusinessOnboarding(businessApi, businessId, input);
      const loaded = await loadBusinesses({
        blockUi: false,
        surfaceError: false,
        preferredActiveId: businessId,
      });
      const created = loaded.find((business) => business.id === businessId);
      if (!created) {
        throw new Error(
          "The business was saved, but the workspace list could not be refreshed.",
        );
      }
      activeBusinessIdRef.current = created.id;
      setActiveBusinessId(created.id);
      writeStoredBusinessId(created.id);
      return created;
    },
    [loadBusinesses],
  );

  const updateBusiness = useCallback(
    async (id: string, input: BusinessInput) => {
      const current = businessesRef.current.find(
        (business) => business.id === id,
      );
      if (!current) {
        throw new Error("This business is not available to your account.");
      }
      let summary;
      try {
        summary = await businessApi.updateProfile(
          id,
          createBusinessProfilePayload(input),
        );
      } catch (reason) {
        throw new Error(
          humanizeApiError(
            reason,
            "We couldn't save this business profile. Please try again.",
          ),
        );
      }
      const updated = {
        ...businessFromSummary(summary),
        theme: current.theme,
        connectedChannels: current.connectedChannels,
        brandIdentity: current.brandIdentity,
      };
      const next = businessesRef.current.map((business) =>
        business.id === id ? updated : business,
      );
      businessesRef.current = next;
      setBusinesses(next);
      return updated;
    },
    [],
  );

  const updateBranding = useCallback(
    async (id: string, identity: BrandIdentity | null) => {
      const current = businessesRef.current.find(
        (business) => business.id === id,
      );
      if (!current) {
        throw new Error("This business is not available to your account.");
      }

      const version = ++brandingVersion.current;
      let response;
      try {
        response = await businessApi.updateBranding(
          id,
          brandingUpdateFromIdentity(identity),
        );
      } catch (reason) {
        throw new Error(
          humanizeApiError(
            reason,
            "We couldn't save this business's branding. Please try again.",
          ),
        );
      }

      const latest = businessesRef.current.find(
        (business) => business.id === id,
      );
      if (!latest) {
        throw new Error("This business is not available to your account.");
      }
      const updated = {
        ...latest,
        brandIdentity: brandingFromResponse(response),
      };

      if (
        isCurrentBrandingResponse(
          id,
          version,
          activeBusinessIdRef.current,
          brandingVersion.current,
        )
      ) {
        const next = businessesRef.current.map((business) =>
          business.id === id ? updated : business,
        );
        businessesRef.current = next;
        setBusinesses(next);
      }
      return updated;
    },
    [],
  );

  const uploadLogo = useCallback(async (id: string, file: File) => {
    const current = businessesRef.current.find(
      (business) => business.id === id,
    );
    if (!current) {
      throw new Error("This business is not available to your account.");
    }

    const version = ++brandingVersion.current;
    let response;
    try {
      response = await businessApi.uploadLogo(id, file);
    } catch (reason) {
      throw new Error(
        humanizeApiError(
          reason,
          "We couldn't upload this business's logo. Please try again.",
        ),
      );
    }

    const latest = businessesRef.current.find((business) => business.id === id);
    if (!latest) {
      throw new Error("This business is not available to your account.");
    }
    const updated = {
      ...latest,
      brandIdentity: brandingFromResponse(response),
    };
    if (
      isCurrentBrandingResponse(
        id,
        version,
        activeBusinessIdRef.current,
        brandingVersion.current,
      )
    ) {
      const next = businessesRef.current.map((business) =>
        business.id === id ? updated : business,
      );
      businessesRef.current = next;
      setBusinesses(next);
    }
    return updated;
  }, []);

  const deleteLogo = useCallback(async (id: string) => {
    const current = businessesRef.current.find(
      (business) => business.id === id,
    );
    if (!current) {
      throw new Error("This business is not available to your account.");
    }

    const version = ++brandingVersion.current;
    try {
      await businessApi.deleteLogo(id);
    } catch (reason) {
      throw new Error(
        humanizeApiError(
          reason,
          "We couldn't remove this business's logo. Please try again.",
        ),
      );
    }

    const latest = businessesRef.current.find((business) => business.id === id);
    if (!latest) {
      throw new Error("This business is not available to your account.");
    }
    const identityWithoutLogo = latest.brandIdentity
      ? {
          primaryColor: latest.brandIdentity.primaryColor,
          secondaryColor: latest.brandIdentity.secondaryColor,
          accentColor: latest.brandIdentity.accentColor,
        }
      : undefined;
    const updated = {
      ...latest,
      brandIdentity: hasIdentityValues(identityWithoutLogo)
        ? identityWithoutLogo
        : undefined,
    };
    if (
      isCurrentBrandingResponse(
        id,
        version,
        activeBusinessIdRef.current,
        brandingVersion.current,
      )
    ) {
      const next = businessesRef.current.map((business) =>
        business.id === id ? updated : business,
      );
      businessesRef.current = next;
      setBusinesses(next);
    }
    return updated;
  }, []);

  return (
    <BusinessContext.Provider
      value={{
        businesses,
        activeBusiness,
        activeBusinessId,
        isLoading:
          status === "bootstrapping" ||
          (status === "authenticated" && !hasLoadedAuthenticatedSession) ||
          isLoading,
        error,
        billing,
        billingLoading,
        billingError,
        reloadBilling,
        selectBusiness,
        reloadBusinesses,
        createBusiness,
        updateBusiness,
        updateBranding,
        uploadLogo,
        deleteLogo,
      }}
    >
      {children}
    </BusinessContext.Provider>
  );
}

function hasIdentityValues(identity: BrandIdentity | undefined): boolean {
  return Boolean(
    identity?.primaryColor ||
    identity?.secondaryColor ||
    identity?.accentColor ||
    identity?.logoUrl ||
    identity?.logo,
  );
}

export function useBusiness() {
  const value = useContext(BusinessContext);
  if (!value) {
    throw new Error("useBusiness must be used within BusinessProvider");
  }
  return value;
}

function readStoredBusinessId(): string | null {
  try {
    return localStorage.getItem(ACTIVE_BUSINESS_KEY);
  } catch {
    return null;
  }
}

function writeStoredBusinessId(value: string): void {
  try {
    if (value) localStorage.setItem(ACTIVE_BUSINESS_KEY, value);
    else localStorage.removeItem(ACTIVE_BUSINESS_KEY);
  } catch {
    // Active selection is a best-effort UI preference only.
  }
}
