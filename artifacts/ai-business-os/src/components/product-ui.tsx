import type {
  ButtonHTMLAttributes,
  CSSProperties,
  ReactNode,
} from "react";
import { X } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import { cx, initials } from "@/lib/product-utils";

export type ButtonVariant =
  | "primary"
  | "green"
  | "secondary"
  | "tertiary"
  | "soft"
  | "ghost"
  | "danger"
  | "destructive";

export type BadgeTone =
  | "success"
  | "neutral"
  | "warning"
  | "danger"
  | "error"
  | "info"
  | "green"
  | "orange"
  | "brown"
  | "rose";

export function Button({
  children,
  variant = "secondary",
  className,
  ...props
}: {
  children: ReactNode;
  variant?: ButtonVariant;
  className?: string;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button className={cx("btn", `btn-${variant}`, className)} {...props}>
      {children}
    </button>
  );
}

export function Badge({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: BadgeTone;
  className?: string;
}) {
  return <span className={cx("status", tone, className)}>{children}</span>;
}

export function PageHeader({
  eyebrow,
  title,
  subtitle,
  action,
  actionClassName,
}: {
  eyebrow?: string;
  title: string;
  subtitle?: string;
  action?: ReactNode;
  actionClassName?: string;
}) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {subtitle && <p className="subtle">{subtitle}</p>}
      </div>
      {action && <div className={cx("toolbar", actionClassName)}>{action}</div>}
    </div>
  );
}

export function Card({
  children,
  className = "",
  pad = true,
  style,
}: {
  children: ReactNode;
  className?: string;
  pad?: boolean;
  style?: CSSProperties;
}) {
  return (
    <div className={cx("card", pad && "card-pad", className)} style={style}>
      {children}
    </div>
  );
}

export function SectionTitle({
  title,
  action,
}: {
  title: string;
  action?: ReactNode;
}) {
  return (
    <div className="section-title">
      <h2>{title}</h2>
      {action}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  secondaryAction,
  compact = false,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: ReactNode;
  secondaryAction?: ReactNode;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div className={cx("empty", compact && "compact-empty", className)}>
      {icon}
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {(action || secondaryAction) && (
        <div className="empty-actions">
          {action}
          {secondaryAction}
        </div>
      )}
    </div>
  );
}

export function Avatar({ name, color = "" }: { name: string; color?: string }) {
  return <span className={cx("avatar", color)}>{initials(name)}</span>;
}

export function Modal({
  title,
  description,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <div
        className={cx("modal", wide && "modal-wide")}
        role="dialog"
        aria-modal="true"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="modal-head">
          <div>
            <h2>{title}</h2>
            {description && <p>{description}</p>}
          </div>
          <button
            className="close-btn"
            aria-label="Close dialog"
            data-testid="button-close-dialog"
            onClick={onClose}
          >
            <X />
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

export function WorkspaceDrawer({
  open,
  eyebrow,
  title,
  description,
  children,
  footer,
  onClose,
  closeDisabled = false,
  testId,
  className,
}: {
  open: boolean;
  eyebrow?: string;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  closeDisabled?: boolean;
  testId?: string;
  className?: string;
}) {
  const requestClose = () => {
    if (!closeDisabled) onClose();
  };

  return (
    <Sheet open={open} modal onOpenChange={(nextOpen) => !nextOpen && requestClose()}>
      <SheetContent
        side="right"
        className={cx("workspace-drawer-panel", className)}
        overlayClassName="workspace-drawer-backdrop"
        closeLabel={`Close ${title}`}
        closeDisabled={closeDisabled}
        closeClassName="workspace-drawer-close"
        closeTestId="button-close-workspace-drawer"
        data-testid={testId}
        role="dialog"
        aria-modal="true"
        onEscapeKeyDown={(event) => closeDisabled && event.preventDefault()}
        onPointerDownOutside={(event) => closeDisabled && event.preventDefault()}
      >
        <header className="workspace-drawer-header">
          {eyebrow && <div className="eyebrow">{eyebrow}</div>}
          <SheetTitle className="workspace-drawer-title">{title}</SheetTitle>
          {description && (
            <SheetDescription className="workspace-drawer-description">
              {description}
            </SheetDescription>
          )}
        </header>
        <div className="workspace-drawer-body">{children}</div>
        {footer && <footer className="workspace-drawer-footer">{footer}</footer>}
      </SheetContent>
    </Sheet>
  );
}
