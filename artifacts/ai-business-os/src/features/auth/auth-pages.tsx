import { useState } from "react";
import { ArrowRight, Check, LockKeyhole, Mail, Sparkles, User } from "lucide-react";
import { Link, useLocation } from "wouter";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button, Card } from "@/components/product-ui";
import { useAuth } from "./auth-context";

const loginSchema = z.object({
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(6, "Use at least 6 characters."),
});

const registerSchema = z.object({
  name: z.string().min(2, "Enter your full name."),
  email: z.string().email("Enter a valid email address."),
  password: z.string().min(8, "Use at least 8 characters."),
  confirm: z.string(),
}).refine((value) => value.password === value.confirm, {
  path: ["confirm"],
  message: "The passwords do not match.",
});

type LoginFields = z.infer<typeof loginSchema>;
type RegisterFields = z.infer<typeof registerSchema>;

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
            <div className="brand-sub">one AI team for your whole business</div>
          </div>
        </div>
        <div className="auth-story-copy">
          <div className="eyebrow">Owner command room</div>
          <h1>Your business keeps moving, even when you step away.</h1>
          <p>
            Observe the work, approve important decisions, and give every AI employee
            the context to improve.
          </p>
          <div className="auth-proof-list">
            {["Business-aware AI team", "Human approval for high-risk actions", "Clear activity and audit history"].map((item) => (
              <div key={item}><Check /> {item}</div>
            ))}
          </div>
        </div>
      </div>
      <div className="auth-form-wrap">
        <Card className="auth-card">
          <div className="auth-icon"><Sparkles /></div>
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
  const [, setLocation] = useLocation();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { register, handleSubmit, formState: { errors } } = useForm<LoginFields>({ resolver: zodResolver(loginSchema), defaultValues: { email: "alexandra@businessos.demo", password: "businessos" } });

  const submit = async (data: LoginFields) => {
    setBusy(true);
    setError("");
    try {
      await login(data.email, data.password);
      setLocation("/dashboard");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to sign in.");
      setBusy(false);
    }
  };

  return (
    <AuthFrame eyebrow="Welcome back" title="Sign in to your command room" subtitle="Continue with the last business you were managing.">
      <form className="auth-form" onSubmit={handleSubmit(submit)} noValidate>
        <div className="field"><label>Email</label><div className="auth-input"><Mail /><input type="email" {...register("email")} /></div>{errors.email && <span className="field-error">{errors.email.message}</span>}</div>
        <div className="field"><label>Password</label><div className="auth-input"><LockKeyhole /><input type="password" {...register("password")} /></div>{errors.password && <span className="field-error">{errors.password.message}</span>}</div>
        {error && <div className="form-error">{error}</div>}
        <Button variant="green" type="submit" disabled={busy} className="auth-submit" data-testid="button-login">{busy ? "Opening workspace…" : "Sign in"} <ArrowRight /></Button>
      </form>
      <p className="auth-switch">New to AI Business OS? <Link href="/register">Create an account</Link></p>
      <div className="prototype-note">Prototype access only. Authentication will be connected to the secure backend later; no password is stored.</div>
    </AuthFrame>
  );
}

export function RegisterPage() {
  const { register } = useAuth();
  const [, setLocation] = useLocation();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { register: registerField, handleSubmit, formState: { errors } } = useForm<RegisterFields>({ resolver: zodResolver(registerSchema) });

  const submit = async (data: RegisterFields) => {
    setBusy(true);
    setError("");
    try {
      await register(data.name, data.email, data.password);
      sessionStorage.setItem("ai-business-os:new-registration", "true");
      setLocation("/onboarding");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to create the account.");
      setBusy(false);
    }
  };

  return (
    <AuthFrame eyebrow="Create your account" title="Build your AI business team" subtitle="Start with your business context, channels, and operating preferences.">
      <form className="auth-form" onSubmit={handleSubmit(submit)} noValidate>
        <div className="field"><label>Full name</label><div className="auth-input"><User /><input placeholder="Alexandra Andria" {...registerField("name")} /></div>{errors.name && <span className="field-error">{errors.name.message}</span>}</div>
        <div className="field"><label>Work email</label><div className="auth-input"><Mail /><input type="email" placeholder="you@company.com" {...registerField("email")} /></div>{errors.email && <span className="field-error">{errors.email.message}</span>}</div>
        <div className="form-grid"><div className="field"><label>Password</label><div className="auth-input"><LockKeyhole /><input type="password" {...registerField("password")} /></div>{errors.password && <span className="field-error">{errors.password.message}</span>}</div><div className="field"><label>Confirm</label><div className="auth-input"><LockKeyhole /><input type="password" {...registerField("confirm")} /></div>{errors.confirm && <span className="field-error">{errors.confirm.message}</span>}</div></div>
        {error && <div className="form-error">{error}</div>}
        <Button variant="green" type="submit" disabled={busy} className="auth-submit" data-testid="button-register">{busy ? "Creating account…" : "Create account"} <ArrowRight /></Button>
      </form>
      <p className="auth-switch">Already have an account? <Link href="/login">Sign in</Link></p>
      <div className="prototype-note">This prototype stores only a local session profile. It never stores the entered password.</div>
    </AuthFrame>
  );
}
