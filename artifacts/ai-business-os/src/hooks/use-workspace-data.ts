import { useCallback, useEffect, useState } from "react";
import { useBusiness } from "@/business-context";
import { workspaceRepository } from "@/services/workspace-repository";
import type { AuditEvent, WorkspaceData } from "@/types/workspace";

export function useWorkspaceData() {
  const { activeBusinessId, activeBusiness } = useBusiness();
  const industry = activeBusiness?.industry ?? "__loading__";
  const [data, setData] = useState(() => workspaceRepository.get(activeBusinessId, industry));

  useEffect(() => {
    setData(workspaceRepository.get(activeBusinessId, industry));
    const sync = (event: Event) => {
      if ((event as CustomEvent<string>).detail === activeBusinessId) {
        setData(workspaceRepository.get(activeBusinessId, industry));
      }
    };
    window.addEventListener("ai-business-os:workspace-change", sync);
    return () => window.removeEventListener("ai-business-os:workspace-change", sync);
  }, [activeBusinessId, industry]);

  const update = useCallback(
    (updater: (current: WorkspaceData) => WorkspaceData) => {
      const next = workspaceRepository.update(activeBusinessId, industry, updater);
      setData(next);
      return next;
    },
    [activeBusinessId, industry],
  );

  const recordAudit = useCallback(
    (event: Omit<AuditEvent, "id" | "timestamp">) => {
      const next = workspaceRepository.addAudit(activeBusinessId, industry, event);
      setData(next);
    },
    [activeBusinessId, industry],
  );

  return { data, update, recordAudit, businessId: activeBusinessId, industry };
}
