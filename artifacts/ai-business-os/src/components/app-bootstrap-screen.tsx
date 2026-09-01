import { AlertTriangle, RefreshCw } from "lucide-react";
import { ProductLogo } from "@/components/product-brand";
import { Button } from "@/components/product-ui";
import { PRODUCT_NAME } from "@/config/brand";
import "./app-bootstrap-screen.css";

type AppBootstrapScreenProps =
  | {
      mode?: "loading";
      error?: never;
      onRetry?: never;
    }
  | {
      mode: "error";
      error: string;
      onRetry: () => void;
    };

const RESTORATION_STAGES = [
  "Secure session",
  "Business context",
  "AI workspace",
] as const;

function IntelligenceMark({ error }: { error: boolean }) {
  return (
    <div
      className="app-bootstrap-visual"
      data-state={error ? "error" : "loading"}
      aria-hidden="true"
    >
      <span className="app-bootstrap-aura" />
      <svg
        className="app-bootstrap-system"
        viewBox="0 0 200 200"
        focusable="false"
      >
        <circle className="app-bootstrap-guide guide-outer" cx="100" cy="100" r="88" />
        <circle className="app-bootstrap-guide guide-middle" cx="100" cy="100" r="69" />
        <circle className="app-bootstrap-guide guide-inner" cx="100" cy="100" r="52" />

        <g className="app-bootstrap-orbit orbit-outer">
          <circle cx="100" cy="100" r="88" pathLength="100" />
          <circle className="app-bootstrap-node" cx="100" cy="12" r="2.4" />
        </g>
        <g className="app-bootstrap-orbit orbit-middle">
          <circle cx="100" cy="100" r="69" pathLength="100" />
          <circle className="app-bootstrap-node" cx="100" cy="31" r="2" />
        </g>
        <g className="app-bootstrap-orbit orbit-inner">
          <circle cx="100" cy="100" r="52" pathLength="100" />
          <circle className="app-bootstrap-node" cx="100" cy="48" r="1.8" />
        </g>
      </svg>

      <span className="app-bootstrap-logo-core">
        <ProductLogo decorative size="lg" />
      </span>

      {error && (
        <span className="app-bootstrap-error-badge">
          <AlertTriangle />
        </span>
      )}
    </div>
  );
}

export function AppBootstrapScreen(props: AppBootstrapScreenProps) {
  const error = props.mode === "error";

  return (
    <main className="app-bootstrap-screen">
      <section
        className="app-bootstrap-content"
        role={error ? "alert" : "status"}
        aria-live={error ? "assertive" : "polite"}
        aria-busy={!error}
      >
        <IntelligenceMark error={error} />

        <h1>
          {error ? `Cannot open ${PRODUCT_NAME}` : `Opening ${PRODUCT_NAME}`}
        </h1>
        <p className="app-bootstrap-description">
          {error ? props.error : "Restoring your secure workspace…"}
        </p>

        {error ? (
          <Button
            type="button"
            variant="green"
            className="app-bootstrap-retry"
            onClick={props.onRetry}
          >
            <RefreshCw />
            Retry connection
          </Button>
        ) : (
          <>
            <p className="app-bootstrap-tertiary">
              Reconnecting your business context and AI workspace.
            </p>

            {/* This sequence is deliberately visual-only; it does not assert backend milestones. */}
            <div className="app-bootstrap-sequence" aria-hidden="true">
              {RESTORATION_STAGES.map((stage) => (
                <span className="app-bootstrap-stage" key={stage}>
                  {stage}
                </span>
              ))}
            </div>
            <div className="app-bootstrap-progress" aria-hidden="true">
              <span />
            </div>
          </>
        )}
      </section>
    </main>
  );
}
