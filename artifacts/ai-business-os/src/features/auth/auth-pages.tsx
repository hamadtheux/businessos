import { useState, type FormEvent } from "react";
import {
  ArrowRight,
  Check,
  LockKeyhole,
  Mail,
  Sparkles,
  User,
} from "lucide-react";
import { Link } from "wouter";
import { z } from "zod";

import { Button, Card } from "@/components/product-ui";
import { useAuth } from "./auth-context";

const loginSchema = z.object({
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(6, "Use at least 6 characters."),
});

const registerSchema = z
  .object({
    name: z.string().min(2, "Enter your full name."),
    email: z.string().email("Enter a valid email address."),
    password: z.string().min(12, "Use at least 12 characters."),
    confirm: z.string(),
  })
  .refine((value) => value.password === value.confirm, {
    path: ["confirm"],
    message: "The passwords do not match.",
  });

type FieldErrors = Record<string, string>;

function getFieldErrors(error: z.ZodError): FieldErrors {
  const result: FieldErrors = {};

  for (const issue of error.issues) {
    const field = issue.path[0];

    if (typeof field === "string" && !result[field]) {
      result[field] = issue.message;
    }
  }

  return result;
}

function AuthFrame({
  eyebrow,
  title,
  subtitle,
  children,
}: {
  eyebrow: string;
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <div className="auth-screen">
      <div className="auth-story">
        <div className="brand auth-brand">
          <div className="brand-mark">AI</div>

          <div>
            <div className="brand-copy">AI Business OS</div>
            <div className="brand-sub">
              one AI team for your whole business
            </div>
          </div>
        </div>

        <div className="auth-story-copy">
          <div className="eyebrow">Owner command room</div>

          <h1>
            Your business keeps moving, even when you step away.
          </h1>

          <p>
            Observe the work, approve important decisions, and give every AI
            employee the context to improve.
          </p>

          <div className="auth-proof-list">
            {[
              "Business-aware AI team",
              "Human approval for high-risk actions",
              "Clear activity and audit history",
            ].map((item) => (
              <div key={item}>
                <Check /> {item}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="auth-form-wrap">
        <Card className="auth-card">
          <div className="auth-icon">
            <Sparkles />
          </div>

          <div className="eyebrow">{eyebrow}</div>

          <h1>{title}</h1>

          <p className="subtle auth-subtitle">{subtitle}</p>

          {children}
        </Card>
      </div>
    </div>
  );
}

export function LoginPage() {
  const { login } = useAuth();

  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (busy) {
      return;
    }

    setError("");

    const form = new FormData(event.currentTarget);

    const result = loginSchema.safeParse({
      email: String(form.get("email") ?? "").trim(),
      password: String(form.get("password") ?? ""),
    });

    if (!result.success) {
      setFieldErrors(getFieldErrors(result.error));
      return;
    }

    setFieldErrors({});
    setBusy(true);

    try {
      await login(result.data.email, result.data.password);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to sign in.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthFrame
      eyebrow="Welcome back"
      title="Sign in to your command room"
      subtitle="Continue with the last business you were managing."
    >
      <form className="auth-form" onSubmit={submit} noValidate>
        <div className="field">
          <label htmlFor="login-email">Email</label>

          <div className="auth-input">
            <Mail />

            <input
              id="login-email"
              name="email"
              type="email"
              autoComplete="email"
            />
          </div>

          {fieldErrors.email && (
            <span className="field-error">
              {fieldErrors.email}
            </span>
          )}
        </div>

        <div className="field">
          <label htmlFor="login-password">Password</label>

          <div className="auth-input">
            <LockKeyhole />

            <input
              id="login-password"
              name="password"
              type="password"
              autoComplete="current-password"
            />
          </div>

          {fieldErrors.password && (
            <span className="field-error">
              {fieldErrors.password}
            </span>
          )}
        </div>

        {error && <div className="form-error">{error}</div>}

        <Button
          variant="green"
          type="submit"
          disabled={busy}
          className="auth-submit"
          data-testid="button-login"
        >
          {busy ? "Opening workspace…" : "Sign in"}
          <ArrowRight />
        </Button>
      </form>

      <p className="auth-switch">
        New to AI Business OS?{" "}
        <Link href="/register">Create an account</Link>
      </p>

      <div className="prototype-note">
        Secure sessions keep the refresh credential protected from browser
        scripts.
      </div>
    </AuthFrame>
  );
}

export function RegisterPage() {
  const { register } = useAuth();

  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (busy) {
      return;
    }

    setError("");

    const form = new FormData(event.currentTarget);

    const result = registerSchema.safeParse({
      name: String(form.get("name") ?? "").trim(),
      email: String(form.get("email") ?? "").trim(),
      password: String(form.get("password") ?? ""),
      confirm: String(form.get("confirm") ?? ""),
    });

    if (!result.success) {
      setFieldErrors(getFieldErrors(result.error));
      return;
    }

    setFieldErrors({});
    setBusy(true);

    try {
      await register(
        result.data.name,
        result.data.email,
        result.data.password,
      );

      try {
        sessionStorage.removeItem(
          "ai-business-os:onboarding-draft:v2",
        );
      } catch {
        // A stale UI draft must never turn successful registration into an error.
      }
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to create the account.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthFrame
      eyebrow="Create your account"
      title="Build your AI business team"
      subtitle="Start with your business context, channels, and operating preferences."
    >
      <form className="auth-form" onSubmit={submit} noValidate>
        <div className="field">
          <label htmlFor="register-name">Full name</label>

          <div className="auth-input">
            <User />

            <input
              id="register-name"
              name="name"
              placeholder="Alexandra Andria"
              autoComplete="name"
            />
          </div>

          {fieldErrors.name && (
            <span className="field-error">
              {fieldErrors.name}
            </span>
          )}
        </div>

        <div className="field">
          <label htmlFor="register-email">Work email</label>

          <div className="auth-input">
            <Mail />

            <input
              id="register-email"
              name="email"
              type="email"
              placeholder="you@company.com"
              autoComplete="email"
            />
          </div>

          {fieldErrors.email && (
            <span className="field-error">
              {fieldErrors.email}
            </span>
          )}
        </div>

        <div className="form-grid">
          <div className="field">
            <label htmlFor="register-password">Password</label>

            <div className="auth-input">
              <LockKeyhole />

              <input
                id="register-password"
                name="password"
                type="password"
                autoComplete="new-password"
              />
            </div>

            {fieldErrors.password && (
              <span className="field-error">
                {fieldErrors.password}
              </span>
            )}
          </div>

          <div className="field">
            <label htmlFor="register-confirm">Confirm</label>

            <div className="auth-input">
              <LockKeyhole />

              <input
                id="register-confirm"
                name="confirm"
                type="password"
                autoComplete="new-password"
              />
            </div>

            {fieldErrors.confirm && (
              <span className="field-error">
                {fieldErrors.confirm}
              </span>
            )}
          </div>
        </div>

        {error && <div className="form-error">{error}</div>}

        <Button
          variant="green"
          type="submit"
          disabled={busy}
          className="auth-submit"
          data-testid="button-register"
        >
          {busy ? "Creating account…" : "Create account"}
          <ArrowRight />
        </Button>
      </form>

      <p className="auth-switch">
        Already have an account?{" "}
        <Link href="/login">Sign in</Link>
      </p>

      <div className="prototype-note">
        Your password is sent only for account creation and secure sign-in. It
        is never saved in browser storage.
      </div>
    </AuthFrame>
  );
}