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
import { businessDraftRepository } from "@/services/business-draft-repository";
import { persistBusinessOnboarding } from "@/services/business-onboarding-persistence";
import {
  applyBrandingToBusinessList,
  brandingUpdateFromIdentity,
  brandingFromResponse,
  businessFromSummary,
  isCurrentBrandingResponse,
  resolveActiveBusinessId,
} from "@/services/business-model";
import type { BrandIdentity, Business, BusinessInput } from "@/types/business";

const ACTIVE_BUSINESS_KEY = "ai-os-active-business";

type BusinessContextValue = {
  businesses: Business[];
  activeBusiness?: Business;
  activeBusinessId: string;
  isLoading: boolean;
  error: string;
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
  const loadVersion = useRef(0);
  const brandingVersion = useRef(0);
  const businessesRef = useRef<Business[]>([]);
  const activeBusinessIdRef = useRef("");

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
      .catch((reason: unknown) => {
        if (
          isCurrentBrandingResponse(
            id,
            version,
            activeBusinessIdRef.current,
            brandingVersion.current,
          )
        ) {
          setError(
            humanizeApiError(
              reason,
              "We couldn't load this business's branding. Please try again.",
            ),
          );
        }
      });
  }, []);

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
          businessFromSummary(summary, businessDraftRepository.get(summary.id)),
        );
        if (version !== loadVersion.current) return loaded;

        const nextActiveId = resolveActiveBusinessId(
          preferredActiveId ?? readStoredBusinessId(),
          loaded,
        );
        const activeBranding = nextActiveId
          ? await businessApi.getBranding(nextActiveId)
          : null;
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
        activeBusinessIdRef.current = nextActiveId;
        setBusinesses(branded);
        setActiveBusinessId(nextActiveId);
        writeStoredBusinessId(nextActiveId);
        return branded;
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
    [],
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

  const createBusiness = useCallback(
    async (input: BusinessInput, businessId?: string) => {
      if (!businessId) {
        throw new Error("A stable business identity is required.");
      }
      await persistBusinessOnboarding(businessApi, businessId, input);
      businessDraftRepository.save(businessId, input);

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
      const draft = businessDraftRepository.save(id, input);
      const updated = {
        ...current,
        ...draft,
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
