// Refresh through the authorized API before the shortest media lifetime elapses.
// Branding uses the existing BusinessProvider/widget fetch paths, not a second cache.
export const BRANDING_REFRESH_INTERVAL_MS = 45_000;

export function startBrandingRefresh(refresh: () => Promise<unknown>) {
  let stopped = false;
  let pending = false;
  const run = async () => {
    if (stopped || pending || document.visibilityState === "hidden") return;
    pending = true;
    try {
      await refresh();
    } catch {
      // Optional presentation refresh must not interrupt the current workspace/chat.
    } finally {
      pending = false;
    }
  };
  const timer = window.setInterval(run, BRANDING_REFRESH_INTERVAL_MS);
  window.addEventListener("focus", run);
  window.addEventListener("online", run);
  document.addEventListener("visibilitychange", run);
  return () => {
    stopped = true;
    window.clearInterval(timer);
    window.removeEventListener("focus", run);
    window.removeEventListener("online", run);
    document.removeEventListener("visibilitychange", run);
  };
}
