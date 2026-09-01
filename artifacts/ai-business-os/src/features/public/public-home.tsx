import { useEffect, useState, type ReactNode } from "react";
import { Link } from "wouter";
import {
  ArrowRight,
  BarChart3,
  Bot,
  BrainCircuit,
  CalendarDays,
  Check,
  ChevronRight,
  CircleCheck,
  Database,
  Globe2,
  Instagram,
  Layers3,
  LineChart,
  Mail,
  Megaphone,
  Menu,
  MessageCircle,
  Network,
  Search,
  ShieldCheck,
  ShoppingBag,
  Sparkles,
  Store,
  Target,
  Users,
  Workflow,
  X,
  Zap,
} from "lucide-react";

import { ProductLogo } from "@/components/product-brand";
import { PRODUCT_NAME } from "@/config/brand";

import "./public-home.css";
import "./public-home-hero.css";

type IconType = typeof BrainCircuit;

const integrations: Array<[string, IconType]> = [
  ["Gmail", Mail],
  ["Calendar", CalendarDays],
  ["Meta", Network],
  ["Instagram", Instagram],
  ["Google Ads", Target],
  ["Messaging", MessageCircle],
  ["Commerce", Store],
  ["Website", Globe2],
];

const operatingSteps = [
  ["01", "Observe", "Signals arrive from your business and connected systems."],
  ["02", "Understand", "9D Brain combines those signals with business context."],
  ["03", "Analyze", "AI roles evaluate what changed and why it matters."],
  ["04", "Recommend", "The system prepares the next useful business move."],
  ["05", "Govern", "Approval and policy controls protect important actions."],
  ["06", "Execute", "Approved work moves through supported workflows."],
] as const;

const faqs = [
  {
    title: "Is 9D Brain just another AI chatbot?",
    body:
      "No. Chat is one interface inside a larger AI business operating system that connects business context, customers, operations, marketing, analytics, workflows and supported integrations.",
  },
  {
    title: "Will AI automatically control my business?",
    body:
      "Consequential actions are designed around governance. Permissions, approvals and provider controls keep important external work under business control.",
  },
  {
    title: "Can I connect tools my business already uses?",
    body:
      "9D Brain is built around supported provider connections. Availability depends on the provider, permissions, account configuration and production authorization.",
  },
  {
    title: "Can I manage multiple businesses?",
    body:
      "Yes. Each business operates in its own isolated workspace with independent context, customers, integrations, AI configuration and operational data.",
  },
];

function useReveal() {
  useEffect(() => {
    const elements = Array.from(
      document.querySelectorAll<HTMLElement>("[data-reveal]"),
    );

    if (
      !elements.length ||
      typeof IntersectionObserver === "undefined" ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      elements.forEach((element) => element.classList.add("is-visible"));
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;

          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      },
      {
        threshold: 0.12,
        rootMargin: "0px 0px -6% 0px",
      },
    );

    elements.forEach((element) => observer.observe(element));

    return () => observer.disconnect();
  }, []);
}

function SectionLabel({
  icon: Icon,
  children,
}: {
  icon: IconType;
  children: ReactNode;
}) {
  return (
    <span className="marketing-label">
      <Icon />
      {children}
    </span>
  );
}

function MarketingBrand() {
  return (
    <Link href="/" className="marketing-brand">
      <ProductLogo size="md" />
      <span>{PRODUCT_NAME}</span>
    </Link>
  );
}

function MarketingButton({
  children,
  secondary = false,
}: {
  children: ReactNode;
  secondary?: boolean;
}) {
  return (
    <Link
      href="/register"
      className={`marketing-button${secondary ? " secondary" : ""}`}
    >
      {children}
      <ArrowRight />
    </Link>
  );
}

function FeatureBullet({ children }: { children: ReactNode }) {
  return (
    <div className="feature-bullet">
      <span>
        <Check />
      </span>
      <p>{children}</p>
    </div>
  );
}

const HERO_V6_CSS = `
.hero6 {
  --h6-green: #1d863a;
  --h6-green-dark: #125d29;
  --h6-lime: #7fca48;
  --h6-ink: #102017;
  --h6-copy: #5f6e64;
  --h6-muted: #89958d;
  --h6-line: rgba(20, 50, 29, 0.09);

  position: relative;
  width: 100%;
  min-height: 790px;
  margin-top: 70px;
  overflow: hidden;
  isolation: isolate;

  border: 1px solid rgba(18, 52, 28, 0.08);
  border-radius: 36px;

  background:
    radial-gradient(
      circle at 50% 24%,
      rgba(127, 202, 72, 0.12),
      transparent 31%
    ),
    linear-gradient(145deg, #ffffff, #f9fcf9 55%, #f4f8f5);

  box-shadow:
    0 42px 110px rgba(16, 43, 24, 0.08),
    0 8px 28px rgba(16, 43, 24, 0.025),
    inset 0 1px #ffffff;
}

.hero6::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;

  opacity: 0.42;

  background-image:
    linear-gradient(rgba(22, 65, 34, 0.026) 1px, transparent 1px),
    linear-gradient(90deg, rgba(22, 65, 34, 0.026) 1px, transparent 1px);

  background-size: 52px 52px;

  mask-image:
    linear-gradient(
      to bottom,
      rgba(0,0,0,.9),
      rgba(0,0,0,.55) 65%,
      transparent
    );
}

.hero6-glow {
  position: absolute;
  left: 50%;
  top: 32%;

  width: 720px;
  height: 390px;

  transform: translateX(-50%);

  border-radius: 50%;

  background: rgba(127, 202, 72, 0.12);

  filter: blur(95px);
  pointer-events: none;
}

.hero6-top {
  position: relative;
  z-index: 5;

  min-height: 72px;

  padding: 20px 26px 16px;

  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;

  border-bottom: 1px solid var(--h6-line);
}

.hero6-top-label {
  display: flex;
  align-items: center;
  gap: 9px;

  color: var(--h6-copy);

  font-size: 11px;
  font-weight: 500;
}

.hero6-top-label svg {
  width: 15px;
  height: 15px;

  color: var(--h6-green);
}

.hero6-top-label strong {
  color: var(--h6-ink);

  font-size: 11px;
  font-weight: 600;
}

.hero6-top-journey {
  display: flex;
  align-items: center;
  gap: 8px;

  color: #708078;

  font-size: 10px;
  font-weight: 600;

  letter-spacing: 0.035em;
  text-transform: uppercase;
}

.hero6-top-journey strong {
  color: var(--h6-green);
  font-weight: 600;
}

.hero6-top-journey svg {
  width: 12px;
  height: 12px;

  color: #a2ada6;
}


/* =========================================================
   MAIN 3-STAGE STORY
   ========================================================= */

.hero6-story {
  position: relative;
  z-index: 4;

  padding: 27px 24px 205px;

  display: grid;

  grid-template-columns:
    minmax(0, 1fr)
    58px
    minmax(0, 1.15fr)
    58px
    minmax(0, 1fr);

  align-items: stretch;

  gap: 10px;
}

.hero6-stage {
  position: relative;

  min-width: 0;
  min-height: 440px;

  padding: 24px;

  border: 1px solid var(--h6-line);
  border-radius: 25px;

  background: rgba(255, 255, 255, 0.83);

  box-shadow:
    0 18px 46px rgba(15, 43, 24, 0.045),
    inset 0 1px #ffffff;

  backdrop-filter: blur(16px);
}

.hero6-stage-number {
  display: inline-flex;

  align-items: center;

  min-height: 25px;

  padding: 0 8px;

  border-radius: 999px;

  background: rgba(29, 134, 58, 0.07);

  color: var(--h6-green);

  font-size: 9px;
  font-weight: 600;

  letter-spacing: 0.06em;
}

.hero6-stage h3 {
  margin: 15px 0 0;

  color: var(--h6-ink);

  font-size: clamp(20px, 1.7vw, 25px);
  font-weight: 600;

  line-height: 1.15;
  letter-spacing: -0.04em;
}

.hero6-stage > p {
  margin: 10px 0 0;

  color: var(--h6-copy);

  font-size: 11px;
  line-height: 1.65;
}


/* =========================================================
   STAGE 1 — YOUR BUSINESS
   ========================================================= */

.hero6-business-channels {
  margin-top: 21px;

  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}

.hero6-channel {
  min-height: 32px;

  padding: 0 9px;

  display: inline-flex;
  align-items: center;
  gap: 6px;

  border: 1px solid rgba(19, 59, 31, 0.07);
  border-radius: 9px;

  background: #f7faf7;

  color: #55675c;

  font-size: 9px;
  font-weight: 500;
}

.hero6-channel svg {
  width: 12px;
  height: 12px;

  color: var(--h6-green);
}

.hero6-business-map {
  margin-top: 19px;

  display: grid;
  gap: 8px;
}

.hero6-business-item {
  min-height: 55px;

  padding: 10px 11px;

  display: grid;

  grid-template-columns:
    auto minmax(0, 1fr);

  align-items: center;

  gap: 10px;

  border: 1px solid rgba(19, 59, 31, 0.065);
  border-radius: 13px;

  background:
    linear-gradient(145deg, #ffffff, #f9fbf9);
}

.hero6-business-icon {
  width: 34px;
  height: 34px;

  display: grid;
  place-items: center;

  border-radius: 10px;

  background: rgba(29, 134, 58, 0.07);

  color: var(--h6-green);
}

.hero6-business-icon svg {
  width: 15px;
  height: 15px;
}

.hero6-business-item strong {
  display: block;

  color: #304239;

  font-size: 10px;
  font-weight: 600;
}

.hero6-business-item small {
  display: block;

  margin-top: 3px;

  color: #87938b;

  font-size: 8px;
  line-height: 1.4;
}


/* =========================================================
   CONNECTOR BEAMS
   ========================================================= */

.hero6-bridge {
  position: relative;

  display: flex;
  align-items: center;
  justify-content: center;
}

.hero6-bridge-line {
  position: relative;

  width: 100%;
  height: 1px;

  background:
    linear-gradient(
      90deg,
      rgba(29, 134, 58, 0.08),
      rgba(29, 134, 58, 0.38),
      rgba(29, 134, 58, 0.08)
    );
}

.hero6-bridge-line::after {
  content: "";

  position: absolute;

  left: 0;
  top: 50%;

  width: 8px;
  height: 8px;

  transform: translateY(-50%);

  border-radius: 50%;

  background: var(--h6-green);

  box-shadow:
    0 0 0 5px rgba(29, 134, 58, 0.07),
    0 0 18px rgba(29, 134, 58, 0.32);

  animation: hero6Packet 2.8s ease-in-out infinite;
}

.hero6-bridge-icon {
  position: absolute;

  width: 31px;
  height: 31px;

  display: grid;
  place-items: center;

  border: 1px solid rgba(29, 134, 58, 0.11);
  border-radius: 50%;

  background: #ffffff;

  color: var(--h6-green);

  box-shadow: 0 8px 20px rgba(16, 43, 24, 0.06);
}

.hero6-bridge-icon svg {
  width: 13px;
  height: 13px;
}


/* =========================================================
   STAGE 2 — 9D BRAIN
   ========================================================= */

.hero6-stage-brain {
  overflow: hidden;

  border-color: rgba(29, 134, 58, 0.13);

  background:
    radial-gradient(
      circle at 50% 10%,
      rgba(127, 202, 72, 0.12),
      transparent 35%
    ),
    linear-gradient(145deg, #fbfefb, #f1f8f3);
}

.hero6-stage-brain::before {
  content: "";

  position: absolute;

  width: 330px;
  height: 330px;

  left: 50%;
  top: 38%;

  transform: translate(-50%, -50%);

  border: 1px dashed rgba(29, 134, 58, 0.09);
  border-radius: 50%;

  animation: hero6Rotate 36s linear infinite;
}

.hero6-brain-box {
  position: relative;
  z-index: 2;

  margin-top: 20px;

  min-height: 136px;

  padding: 17px;

  border: 1px solid rgba(29, 134, 58, 0.12);
  border-radius: 18px;

  background: rgba(255, 255, 255, 0.85);

  box-shadow:
    0 17px 40px rgba(29, 134, 58, 0.07);

  text-align: center;
}

.hero6-brain-logo {
  width: 44px;
  height: 44px;

  margin: 0 auto;

  display: grid;
  place-items: center;
}

.hero6-brain-box > strong {
  display: block;

  margin-top: 7px;

  color: #193823;

  font-size: 15px;
  font-weight: 600;
}

.hero6-brain-box > p {
  margin: 5px auto 0;

  max-width: 280px;

  color: #68776d;

  font-size: 9px;
  line-height: 1.5;
}

.hero6-memory {
  margin-top: 11px;

  display: flex;
  justify-content: center;
  flex-wrap: wrap;
  gap: 5px;
}

.hero6-memory span {
  padding: 4px 7px;

  border-radius: 999px;

  background: rgba(29, 134, 58, 0.06);

  color: #5f7b66;

  font-size: 7px;
  font-weight: 500;
}

.hero6-ai-title {
  position: relative;
  z-index: 2;

  margin-top: 18px;

  display: flex;
  align-items: center;
  gap: 7px;

  color: #5f7466;

  font-size: 9px;
  font-weight: 600;

  letter-spacing: 0.045em;
  text-transform: uppercase;
}

.hero6-ai-title svg {
  width: 13px;
  height: 13px;

  color: var(--h6-green);
}

.hero6-agents {
  position: relative;
  z-index: 2;

  margin-top: 9px;

  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));

  gap: 7px;
}

.hero6-agent {
  min-height: 46px;

  padding: 8px;

  display: flex;
  align-items: center;
  gap: 8px;

  border: 1px solid rgba(29, 134, 58, 0.08);
  border-radius: 11px;

  background: rgba(255, 255, 255, 0.78);
}

.hero6-agent svg {
  flex: 0 0 auto;

  width: 27px;
  height: 27px;

  padding: 7px;

  border-radius: 8px;

  background: rgba(29, 134, 58, 0.07);

  color: var(--h6-green);
}

.hero6-agent strong {
  display: block;

  color: #36483d;

  font-size: 9px;
  font-weight: 600;
}

.hero6-agent small {
  display: block;

  margin-top: 2px;

  color: #8a968e;

  font-size: 7px;
}

.hero6-governance {
  position: relative;
  z-index: 2;

  margin-top: 11px;

  min-height: 35px;

  padding: 0 10px;

  display: flex;
  align-items: center;
  justify-content: center;

  gap: 7px;

  border: 1px solid rgba(29, 134, 58, 0.09);
  border-radius: 10px;

  background: rgba(29, 134, 58, 0.045);

  color: #607568;

  font-size: 8px;
  font-weight: 500;
}

.hero6-governance svg {
  width: 12px;
  height: 12px;

  color: var(--h6-green);
}


/* =========================================================
   STAGE 3 — THE WORK
   ========================================================= */

.hero6-work-list {
  margin-top: 20px;

  display: grid;
  gap: 8px;
}

.hero6-work {
  min-height: 57px;

  padding: 10px 11px;

  display: grid;

  grid-template-columns:
    auto minmax(0, 1fr) auto;

  align-items: center;

  gap: 10px;

  border: 1px solid rgba(18, 52, 28, 0.07);
  border-radius: 13px;

  background: #ffffff;

  transition:
    transform 180ms ease,
    border-color 180ms ease,
    box-shadow 180ms ease;
}

.hero6-work:hover {
  transform: translateX(3px);

  border-color: rgba(29, 134, 58, 0.16);

  box-shadow: 0 11px 28px rgba(17, 44, 25, 0.05);
}

.hero6-work-icon {
  width: 34px;
  height: 34px;

  display: grid;
  place-items: center;

  border-radius: 10px;

  background: rgba(29, 134, 58, 0.07);

  color: var(--h6-green);
}

.hero6-work-icon svg {
  width: 15px;
  height: 15px;
}

.hero6-work strong {
  display: block;

  color: #304239;

  font-size: 10px;
  font-weight: 600;
}

.hero6-work small {
  display: block;

  margin-top: 3px;

  color: #86928a;

  font-size: 8px;
  line-height: 1.4;
}

.hero6-work > svg {
  width: 13px;
  height: 13px;

  color: #a2ada5;
}


/* =========================================================
   REAL EXAMPLES
   ========================================================= */

.hero6-examples {
  position: absolute;
  z-index: 8;

  left: 24px;
  right: 24px;
  bottom: 72px;

  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));

  gap: 9px;
}

.hero6-example {
  min-width: 0;
  min-height: 88px;

  padding: 13px 14px;

  border: 1px solid rgba(18, 52, 28, 0.08);
  border-radius: 15px;

  background: rgba(255, 255, 255, 0.9);

  box-shadow:
    0 13px 34px rgba(16, 43, 24, 0.05);

  backdrop-filter: blur(14px);

  animation: hero6ExampleFloat 6s ease-in-out infinite;
}

.hero6-example:nth-child(2) {
  animation-delay: -2s;
}

.hero6-example:nth-child(3) {
  animation-delay: -4s;
}

.hero6-example > small {
  color: #8c9890;

  font-size: 8px;
  font-weight: 500;
}

.hero6-example > strong {
  display: block;

  margin-top: 4px;

  color: #2c4034;

  font-size: 10px;
  font-weight: 600;
}

.hero6-example-flow {
  margin-top: 10px;

  display: flex;
  align-items: center;

  gap: 6px;

  overflow: hidden;
}

.hero6-example-flow span {
  min-width: 0;

  display: inline-flex;
  align-items: center;
  gap: 5px;

  color: #5d7064;

  font-size: 8px;
  font-weight: 500;

  white-space: nowrap;
}

.hero6-example-flow span:last-child {
  color: var(--h6-green-dark);
  font-weight: 600;
}

.hero6-example-flow svg {
  flex: 0 0 auto;

  width: 11px;
  height: 11px;

  color: var(--h6-green);
}

.hero6-example-flow > svg {
  color: #a0aaa4;
}


/* =========================================================
   FULL PRODUCT BREADTH
   ========================================================= */

.hero6-feature-rail {
  position: absolute;
  z-index: 9;

  left: 17px;
  right: 17px;
  bottom: 16px;

  height: 42px;

  overflow: hidden;

  border: 1px solid rgba(18, 52, 28, 0.075);
  border-radius: 13px;

  background: rgba(250, 252, 250, 0.94);

  box-shadow:
    0 9px 24px rgba(16, 43, 24, 0.035);
}

.hero6-feature-rail::before,
.hero6-feature-rail::after {
  content: "";

  position: absolute;
  z-index: 4;

  top: 0;
  bottom: 0;

  width: 80px;

  pointer-events: none;
}

.hero6-feature-rail::before {
  left: 0;

  background: linear-gradient(90deg, #fbfcfb, transparent);
}

.hero6-feature-rail::after {
  right: 0;

  background: linear-gradient(-90deg, #fbfcfb, transparent);
}

.hero6-feature-track {
  width: max-content;
  height: 100%;

  display: flex;
  align-items: center;

  animation: hero6Marquee 38s linear infinite;
}

.hero6-feature-track span {
  min-height: 100%;

  padding: 0 14px;

  display: inline-flex;
  align-items: center;

  gap: 7px;

  border-right: 1px solid rgba(18, 52, 28, 0.055);

  color: #627168;

  font-size: 9px;
  font-weight: 500;

  white-space: nowrap;
}

.hero6-feature-track i {
  width: 5px;
  height: 5px;

  border-radius: 50%;

  background: var(--h6-green);

  box-shadow: 0 0 0 4px rgba(29, 134, 58, 0.055);
}


/* =========================================================
   MOTION
   ========================================================= */

@keyframes hero6Packet {
  0% {
    left: 0;
    opacity: 0;
  }

  15% {
    opacity: 1;
  }

  85% {
    opacity: 1;
  }

  100% {
    left: calc(100% - 8px);
    opacity: 0;
  }
}

@keyframes hero6Rotate {
  to {
    transform:
      translate(-50%, -50%)
      rotate(360deg);
  }
}

@keyframes hero6ExampleFloat {
  0%,
  100% {
    translate: 0 0;
  }

  50% {
    translate: 0 -4px;
  }
}

@keyframes hero6Marquee {
  to {
    transform: translateX(-50%);
  }
}


/* =========================================================
   TABLET
   ========================================================= */

@media (max-width: 1050px) {
  .hero6-story {
    grid-template-columns:
      minmax(0, 1fr)
      38px
      minmax(0, 1.1fr)
      38px
      minmax(0, 1fr);

    padding-inline: 17px;
  }

  .hero6-stage {
    padding: 18px;
  }

  .hero6-stage h3 {
    font-size: 20px;
  }

  .hero6-channel {
    font-size: 8px;
  }

  .hero6-agent {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
  }
}


/* =========================================================
   MOBILE / SMALL TABLET
   ========================================================= */

@media (max-width: 820px) {
  .hero6 {
    min-height: auto;
    border-radius: 27px;
  }

  .hero6-top {
    padding-inline: 18px;
  }

  .hero6-top-journey {
    display: none;
  }

  .hero6-story {
    padding:
      20px
      16px
      18px;

    display: grid;

    grid-template-columns: 1fr;

    gap: 12px;
  }

  .hero6-stage {
    min-height: 0;
  }

  .hero6-bridge {
    height: 52px;
  }

  .hero6-bridge-line {
    width: 1px;
    height: 100%;

    background:
      linear-gradient(
        180deg,
        rgba(29, 134, 58, 0.08),
        rgba(29, 134, 58, 0.38),
        rgba(29, 134, 58, 0.08)
      );
  }

  .hero6-bridge-line::after {
    left: 50%;
    top: 0;

    transform: translateX(-50%);

    animation: hero6PacketVertical 2.8s ease-in-out infinite;
  }

  .hero6-examples,
  .hero6-feature-rail {
    position: relative;

    left: auto;
    right: auto;
    bottom: auto;
  }

  .hero6-examples {
    margin:
      0
      16px
      12px;

    grid-template-columns: 1fr;
  }

  .hero6-feature-rail {
    margin:
      0
      10px
      10px;
  }

  .hero6-stage-brain::before {
    width: 390px;
    height: 390px;
  }
}

@keyframes hero6PacketVertical {
  0% {
    top: 0;
    opacity: 0;
  }

  15% {
    opacity: 1;
  }

  85% {
    opacity: 1;
  }

  100% {
    top: calc(100% - 8px);
    opacity: 0;
  }
}


/* =========================================================
   PHONE
   ========================================================= */

@media (max-width: 520px) {
  .hero6 {
    margin-top: 47px;
    border-radius: 21px;
  }

  .hero6-top {
    min-height: 62px;
  }

  .hero6-stage {
    padding: 17px;
    border-radius: 19px;
  }

  .hero6-stage h3 {
    font-size: 21px;
  }

  .hero6-stage > p {
    font-size: 11px;
  }

  .hero6-business-channels {
    gap: 6px;
  }

  .hero6-agents {
    grid-template-columns: 1fr;
  }

  .hero6-example-flow {
    flex-wrap: wrap;
  }
}


/* =========================================================
   ACCESSIBILITY
   ========================================================= */

@media (prefers-reduced-motion: reduce) {
  .hero6 *,
  .hero6 *::before,
  .hero6 *::after {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
  }

  .hero6-feature-track {
    animation: none !important;
  }
}
`;

function HeroVisual() {
  const channels = [
    { icon: Mail, label: "Gmail" },
    { icon: MessageCircle, label: "WhatsApp" },
    { icon: CalendarDays, label: "Calendar" },
    { icon: Globe2, label: "Website" },
    { icon: Instagram, label: "Meta" },
    { icon: Target, label: "Google Ads" },
    { icon: ShoppingBag, label: "Commerce" },
  ];

  const businessAreas = [
    {
      icon: Users,
      title: "Customers + Sales",
      detail: "CRM · leads · opportunities · customer history",
    },
    {
      icon: Megaphone,
      title: "Marketing",
      detail: "Campaigns · content · social · advertising",
    },
    {
      icon: ShoppingBag,
      title: "Commerce + Operations",
      detail: "Products · orders · vendors · suppliers",
    },
    {
      icon: MessageCircle,
      title: "Conversations",
      detail: "Gmail · WhatsApp · support · website inquiries",
    },
    {
      icon: CalendarDays,
      title: "Schedule + Business Activity",
      detail: "Calendar · appointments · workflows · records",
    },
  ];

  const agents = [
    ["Business Manager", "Coordinates priorities", BrainCircuit],
    ["AI CMO", "Marketing + growth", Megaphone],
    ["Sales AI", "Leads + opportunities", Target],
    ["Support AI", "Customer experience", MessageCircle],
    ["Operations AI", "Orders + workflows", Workflow],
    ["Analytics AI", "Evidence + intelligence", BarChart3],
  ] as const;

  const work = [
    {
      icon: Target,
      title: "Grow revenue",
      detail: "Prioritize leads, opportunities and intelligent follow-up",
    },
    {
      icon: Megaphone,
      title: "Run marketing",
      detail: "Prepare campaigns, content, social and advertising decisions",
    },
    {
      icon: MessageCircle,
      title: "Support customers",
      detail: "Unify conversations, support and your Website AI assistant",
    },
    {
      icon: Store,
      title: "Coordinate operations",
      detail: "Orders, vendors, suppliers, scheduling and business workflows",
    },
    {
      icon: ShieldCheck,
      title: "Automate with control",
      detail: "Automations, approvals, permissions and governed actions",
    },
    {
      icon: BarChart3,
      title: "Measure + improve",
      detail: "Analytics, reports, opportunities, competitors and trends",
    },
  ];

  const allFeatures = [
    "Business Brain",
    "AI Workforce",
    "AI CMO",
    "CRM",
    "Customers",
    "Conversations",
    "Leads",
    "Opportunities",
    "Orders",
    "Vendors",
    "Campaigns",
    "Social",
    "Automations",
    "Approvals",
    "Analytics",
    "Daily Reports",
    "Competitors",
    "Trends",
    "Website AI",
    "Integrations",
  ];

  return (
    <>
      <style>{HERO_V6_CSS}</style>

      <div
        className="hero6"
        aria-label="How 9D Brain understands and operates a business"
      >
        <div className="hero6-glow" />

        <div className="hero6-top">
          <div className="hero6-top-label">
            <Sparkles />

            <span>
              <strong>How 9D Brain works:</strong>{" "}
              your whole business becomes one intelligent operating system.
            </span>
          </div>

          <div className="hero6-top-journey">
            <strong>Connect</strong>
            <ArrowRight />
            Understand
            <ArrowRight />
            Run
          </div>
        </div>

        <div className="hero6-story">
          <section className="hero6-stage">
            <span className="hero6-stage-number">01 · YOUR BUSINESS</span>

            <h3>
              Connect the business you already run.
            </h3>

            <p>
              9D Brain brings customers, channels, marketing, operations and
              business activity into one business-scoped workspace.
            </p>

            <div className="hero6-business-channels">
              {channels.map(({ icon: Icon, label }) => (
                <span className="hero6-channel" key={label}>
                  <Icon />
                  {label}
                </span>
              ))}
            </div>

            <div className="hero6-business-map">
              {businessAreas.map(({ icon: Icon, title, detail }) => (
                <div className="hero6-business-item" key={title}>
                  <span className="hero6-business-icon">
                    <Icon />
                  </span>

                  <div>
                    <strong>{title}</strong>
                    <small>{detail}</small>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <div className="hero6-bridge">
            <div className="hero6-bridge-line" />

            <span className="hero6-bridge-icon">
              <ArrowRight />
            </span>
          </div>

          <section className="hero6-stage hero6-stage-brain">
            <span className="hero6-stage-number">02 · 9D BRAIN</span>

            <h3>
              One brain understands the whole business.
            </h3>

            <p>
              Instead of isolated AI chats, every AI role works from shared
              business knowledge, memory and operating context.
            </p>

            <div className="hero6-brain-box">
              <div className="hero6-brain-logo">
                <ProductLogo size="lg" />
              </div>

              <strong>Business Brain</strong>

              <p>
                The shared context behind every recommendation, workflow and
                AI role.
              </p>

              <div className="hero6-memory">
                <span>Brand</span>
                <span>Products</span>
                <span>Customers</span>
                <span>History</span>
                <span>Operations</span>
              </div>
            </div>

            <div className="hero6-ai-title">
              <Bot />
              Your AI team
            </div>

            <div className="hero6-agents">
              {agents.map(([name, detail, Icon]) => (
                <div className="hero6-agent" key={name}>
                  <Icon />

                  <div>
                    <strong>{name}</strong>
                    <small>{detail}</small>
                  </div>
                </div>
              ))}
            </div>

            <div className="hero6-governance">
              <ShieldCheck />
              Important actions stay behind permissions, policy and approval.
            </div>
          </section>

          <div className="hero6-bridge">
            <div className="hero6-bridge-line" />

            <span className="hero6-bridge-icon">
              <ArrowRight />
            </span>
          </div>

          <section className="hero6-stage">
            <span className="hero6-stage-number">03 · WORK GETS DONE</span>

            <h3>
              Your AI team helps run the business.
            </h3>

            <p>
              9D Brain turns shared context into recommendations, workflows and
              governed execution across the business.
            </p>

            <div className="hero6-work-list">
              {work.map(({ icon: Icon, title, detail }) => (
                <div className="hero6-work" key={title}>
                  <span className="hero6-work-icon">
                    <Icon />
                  </span>

                  <div>
                    <strong>{title}</strong>
                    <small>{detail}</small>
                  </div>

                  <ChevronRight />
                </div>
              ))}
            </div>
          </section>
        </div>

        <div className="hero6-examples">
          <article className="hero6-example">
            <small>Sales automation example</small>
            <strong>A customer inquiry becomes a real opportunity.</strong>

            <div className="hero6-example-flow">
              <span>
                <Mail />
                Gmail / Website
              </span>

              <ArrowRight />

              <span>
                <Target />
                Sales AI
              </span>

              <ArrowRight />

              <span>
                <Users />
                CRM follow-up
              </span>
            </div>
          </article>

          <article className="hero6-example">
            <small>Marketing automation example</small>
            <strong>Performance becomes the next marketing move.</strong>

            <div className="hero6-example-flow">
              <span>
                <Target />
                Ads + Meta
              </span>

              <ArrowRight />

              <span>
                <Megaphone />
                AI CMO
              </span>

              <ArrowRight />

              <span>
                <Sparkles />
                Campaign
              </span>
            </div>
          </article>

          <article className="hero6-example">
            <small>Operations automation example</small>
            <strong>A request becomes coordinated business work.</strong>

            <div className="hero6-example-flow">
              <span>
                <MessageCircle />
                WhatsApp
              </span>

              <ArrowRight />

              <span>
                <Workflow />
                Operations AI
              </span>

              <ArrowRight />

              <span>
                <Store />
                Order + vendor
              </span>
            </div>
          </article>
        </div>

        <div className="hero6-feature-rail">
          <div className="hero6-feature-track">
            {[...allFeatures, ...allFeatures].map((feature, index) => (
              <span key={`${feature}-${index}`}>
                <i />
                {feature}
              </span>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}


function WorkforceVisual() {
  const roles = [
    {
      icon: BrainCircuit,
      name: "Business Manager",
      copy: "Coordinates priorities across the active business.",
      state: "Operating",
    },
    {
      icon: Megaphone,
      name: "AI CMO",
      copy: "Prepares marketing strategy, campaigns and content.",
      state: "Ready",
    },
    {
      icon: Target,
      name: "Sales AI",
      copy: "Finds and prioritizes high-intent opportunities.",
      state: "3 signals",
    },
    {
      icon: BarChart3,
      name: "Analytics AI",
      copy: "Explains performance changes and business signals.",
      state: "Analyzing",
    },
  ];

  return (
    <div className="product-window">
      <div className="product-window-top">
        <span className="window-dots">
          <i />
          <i />
          <i />
        </span>

        <small>AI Workforce</small>

        <span className="window-live">
          <i />
          Operating
        </span>
      </div>

      <div className="workforce-layout">
        <div className="business-objective">
          <span>Current business objective</span>

          <h3>
            Grow qualified demand while protecting customer experience.
          </h3>

          <div>
            <small>Growth</small>
            <small>Customer experience</small>
            <small>Governed execution</small>
          </div>
        </div>

        <div className="agent-list">
          {roles.map(({ icon: Icon, name, copy, state }) => (
            <article className="agent-item" key={name}>
              <span className="agent-icon">
                <Icon />
              </span>

              <div>
                <strong>{name}</strong>
                <p>{copy}</p>
              </div>

              <small>{state}</small>
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}

function BrainVisual() {
  return (
    <div className="brain-map">
      <div className="brain-map-grid" />

      <div className="brain-map-ring ring-a" />
      <div className="brain-map-ring ring-b" />

      <div className="brain-map-core">
        <BrainCircuit />
        <strong>Business Brain</strong>
        <span>Shared context</span>
      </div>

      <div className="brain-source source-brand">
        <Sparkles />
        <div>
          <strong>Brand</strong>
          <span>Voice + positioning</span>
        </div>
      </div>

      <div className="brain-source source-catalog">
        <Store />
        <div>
          <strong>Catalog</strong>
          <span>Products + services</span>
        </div>
      </div>

      <div className="brain-source source-customers">
        <Users />
        <div>
          <strong>Customers</strong>
          <span>CRM + conversations</span>
        </div>
      </div>

      <div className="brain-source source-operations">
        <Layers3 />
        <div>
          <strong>Operations</strong>
          <span>Workflows + records</span>
        </div>
      </div>

      <div className="brain-source source-performance">
        <LineChart />
        <div>
          <strong>Performance</strong>
          <span>Analytics + signals</span>
        </div>
      </div>

      <div className="brain-source source-connections">
        <Network />
        <div>
          <strong>Integrations</strong>
          <span>Authorized providers</span>
        </div>
      </div>
    </div>
  );
}

function CmoVisual() {
  return (
    <div className="product-window">
      <div className="product-window-top">
        <span className="window-dots">
          <i />
          <i />
          <i />
        </span>

        <small>AI CMO · Campaign Studio</small>

        <span className="soft-pill">Draft</span>
      </div>

      <div className="cmo-layout">
        <div className="cmo-steps">
          <div className="cmo-step complete">
            <span>01</span>
            <div>
              <strong>Goal</strong>
              <small>Grow qualified demand</small>
            </div>
            <Check />
          </div>

          <div className="cmo-step complete">
            <span>02</span>
            <div>
              <strong>Strategy</strong>
              <small>Positioning + audience</small>
            </div>
            <Check />
          </div>

          <div className="cmo-step active">
            <span>03</span>
            <div>
              <strong>Creative</strong>
              <small>Generating variants</small>
            </div>
            <Sparkles />
          </div>

          <div className="cmo-step">
            <span>04</span>
            <div>
              <strong>Approval</strong>
              <small>Owner review</small>
            </div>
          </div>

          <div className="cmo-step">
            <span>05</span>
            <div>
              <strong>Execute</strong>
              <small>Supported channels</small>
            </div>
          </div>
        </div>

        <div className="creative-board">
          <div className="creative-orb orb-one" />
          <div className="creative-orb orb-two" />

          <span className="creative-tag">Campaign concept</span>

          <div className="creative-copy">
            <small>Prepared by 9D Brain</small>

            <h3>
              Turn business context into the next growth move.
            </h3>

            <p>
              Brand-aware creative direction, messaging, CTA and channel
              variants prepared for approval.
            </p>
          </div>

          <div className="creative-channels">
            <span>
              <Instagram />
              Social
            </span>

            <span>
              <Mail />
              Email
            </span>

            <span>
              <Target />
              Ads
            </span>

            <span>
              <Globe2 />
              Website
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

function CustomerVisual() {
  return (
    <div className="product-window">
      <div className="product-window-top">
        <span className="window-dots">
          <i />
          <i />
          <i />
        </span>

        <small>Customer Operating View</small>

        <span className="soft-pill">AI context ready</span>
      </div>

      <div className="customer-layout">
        <aside className="customer-list">
          <div className="customer-search">
            <Search />
            Search customers
          </div>

          <div className="customer-row active">
            <span>AR</span>
            <div>
              <strong>Alex Rivera</strong>
              <small>Interested in the Pro workspace…</small>
            </div>
          </div>

          <div className="customer-row">
            <span>MK</span>
            <div>
              <strong>Maya Khan</strong>
              <small>Asked about service availability…</small>
            </div>
          </div>

          <div className="customer-row">
            <span>SB</span>
            <div>
              <strong>Studio Beta</strong>
              <small>New website inquiry…</small>
            </div>
          </div>
        </aside>

        <div className="customer-conversation">
          <div className="conversation-head">
            <div>
              <strong>Alex Rivera</strong>
              <small>Website · Open opportunity</small>
            </div>

            <span>
              <Sparkles />
              AI brief
            </span>
          </div>

          <div className="message left">
            We are growing quickly. Which setup gives us more automation?
          </div>

          <div className="message right">
            Pro is designed for businesses that need deeper AI, integrations
            and workflow capacity.
          </div>

          <div className="opportunity">
            <span>
              <Target />
            </span>

            <div>
              <strong>High-intent opportunity</strong>
              <p>
                Recommend follow-up and create an opportunity after owner
                review.
              </p>
            </div>

            <button type="button">Review</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function AutomationVisual() {
  return (
    <div className="automation-map">
      <svg viewBox="0 0 900 460" aria-hidden="true">
        <path d="M140 230 H295" />
        <path d="M370 230 H525" />
        <path d="M600 230 H755" />

        <path className="moving one" d="M140 230 H295" />
        <path className="moving two" d="M370 230 H525" />
        <path className="moving three" d="M600 230 H755" />
      </svg>

      <div className="automation-node node-one">
        <span>
          <MessageCircle />
        </span>
        <strong>Signal</strong>
        <p>Customer or business event</p>
      </div>

      <div className="automation-node node-two">
        <span>
          <BrainCircuit />
        </span>
        <strong>Understand</strong>
        <p>AI evaluates business context</p>
      </div>

      <div className="automation-node node-three">
        <span>
          <ShieldCheck />
        </span>
        <strong>Govern</strong>
        <p>Policy and approval gate</p>
      </div>

      <div className="automation-node node-four">
        <span>
          <Zap />
        </span>
        <strong>Execute</strong>
        <p>Authorized business action</p>
      </div>

      <div className="automation-caption">
        <Sparkles />
        Advanced workflow controls remain available when needed.
      </div>
    </div>
  );
}

function IntelligenceVisual() {
  return (
    <div className="product-window">
      <div className="product-window-top">
        <span className="window-dots">
          <i />
          <i />
          <i />
        </span>

        <small>Business Intelligence</small>

        <span className="soft-pill">Current period</span>
      </div>

      <div className="intelligence-layout">
        <div className="intelligence-chart">
          <div className="chart-head">
            <div>
              <small>Business momentum</small>
              <strong>Direction, not dashboard noise.</strong>
            </div>

            <LineChart />
          </div>

          <svg viewBox="0 0 640 270">
            <defs>
              <linearGradient id="brainChartArea" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor="#1D863A" stopOpacity="0.22" />
                <stop offset="100%" stopColor="#1D863A" stopOpacity="0" />
              </linearGradient>
            </defs>

            <path
              className="chart-area"
              d="M0 220 C85 206 110 175 170 182 C225 188 260 122 320 137 C380 152 425 88 487 105 C540 120 585 60 640 48 L640 270 L0 270 Z"
            />

            <path
              className="chart-line"
              d="M0 220 C85 206 110 175 170 182 C225 188 260 122 320 137 C380 152 425 88 487 105 C540 120 585 60 640 48"
            />
          </svg>
        </div>

        <div className="intelligence-cards">
          <article className="intelligence-card featured">
            <span>
              <Sparkles />
              AI daily brief
            </span>

            <strong>
              What changed → why it matters → what to do next.
            </strong>

            <p>
              Business evidence becomes an operating brief instead of another
              wall of charts.
            </p>
          </article>

          <article className="intelligence-card">
            <span>Opportunity intelligence</span>
            <strong>Prioritized signals</strong>
          </article>

          <article className="intelligence-card">
            <span>Competitor intelligence</span>
            <strong>Evidence before conclusion</strong>
          </article>

          <article className="intelligence-card">
            <span>Trend intelligence</span>
            <strong>Changes worth attention</strong>
          </article>
        </div>
      </div>
    </div>
  );
}

function WebsiteAiVisual() {
  return (
    <div className="browser-mockup">
      <div className="browser-head">
        <span>
          <i />
          <i />
          <i />
        </span>

        <small>yourbusiness.com</small>
      </div>

      <div className="browser-body">
        <div className="fake-site-nav">
          <span />

          <div>
            <i />
            <i />
            <i />
          </div>
        </div>

        <div className="fake-site-copy">
          <i className="title" />
          <i />
          <i className="short" />
          <span />
        </div>

        <div className="fake-site-products">
          <i />
          <i />
          <i />
        </div>

        <div className="website-widget">
          <div className="widget-head">
            <div>
              <Bot />

              <span>
                <strong>Your AI assistant</strong>
                <small>Powered by your Business Brain</small>
              </span>
            </div>

            <i />
          </div>

          <div className="widget-body">
            <p>Hi — what can I help you find today?</p>

            <p className="user">
              Which option is best for my team?
            </p>

            <p>
              I can compare the available options using this business&apos;s
              actual catalog and guidance.
            </p>
          </div>

          <div className="widget-input">
            <span>Ask anything…</span>
            <ArrowRight />
          </div>
        </div>
      </div>
    </div>
  );
}

function StorySection({
  id,
  label,
  icon,
  title,
  description,
  bullets,
  visual,
  reverse = false,
}: {
  id?: string;
  label: string;
  icon: IconType;
  title: ReactNode;
  description: string;
  bullets: string[];
  visual: ReactNode;
  reverse?: boolean;
}) {
  return (
    <section className="story-section" id={id}>
      <div className="marketing-container">
        <div
          className={`story-layout${reverse ? " reverse" : ""}`}
          data-reveal
        >
          <div className="story-copy">
            <SectionLabel icon={icon}>{label}</SectionLabel>

            <h2>{title}</h2>

            <p>{description}</p>

            <div className="story-bullets">
              {bullets.map((bullet) => (
                <FeatureBullet key={bullet}>{bullet}</FeatureBullet>
              ))}
            </div>
          </div>

          <div className="story-visual">{visual}</div>
        </div>
      </div>
    </section>
  );
}

export function MarketingHomePage() {
  const [menuOpen, setMenuOpen] = useState(false);

  useReveal();

  return (
    <div className="marketing-page">
      <header className="marketing-header">
        <div className="marketing-container marketing-nav">
          <MarketingBrand />

          <nav className="desktop-nav" aria-label="Primary navigation">
            <a href="#platform">Platform</a>
            <a href="#workforce">AI Workforce</a>
            <a href="#brain">Business Brain</a>
            <a href="#automations">Automations</a>
            <a href="#pricing">Pricing</a>
          </nav>

          <div className="desktop-actions">
            <Link href="/login" className="nav-login">
              Sign in
            </Link>

            <MarketingButton>Start free</MarketingButton>
          </div>

          <button
            type="button"
            className="mobile-menu-button"
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((current) => !current)}
          >
            {menuOpen ? <X /> : <Menu />}
          </button>
        </div>

        {menuOpen && (
          <div className="mobile-nav">
            <a href="#platform" onClick={() => setMenuOpen(false)}>
              Platform
            </a>
            <a href="#workforce" onClick={() => setMenuOpen(false)}>
              AI Workforce
            </a>
            <a href="#brain" onClick={() => setMenuOpen(false)}>
              Business Brain
            </a>
            <a href="#automations" onClick={() => setMenuOpen(false)}>
              Automations
            </a>
            <a href="#pricing" onClick={() => setMenuOpen(false)}>
              Pricing
            </a>

            <div>
              <Link href="/login">Sign in</Link>
              <MarketingButton>Start free</MarketingButton>
            </div>
          </div>
        )}
      </header>

      <main>
        <section className="marketing-hero">
          <div className="hero-background-grid" />
          <div className="hero-background-glow glow-left" />
          <div className="hero-background-glow glow-right" />

          <div className="marketing-container">
            <div className="marketing-hero-copy" data-reveal>
              <div className="hero-eyebrow">
                <span>
                  <Sparkles />
                  AI Business Operating System
                </span>

                <i />

                <span className="hero-eyebrow-secondary">
                  Built for business operations
                </span>
              </div>

              <h1>
                One AI brain.
                <br />
                <span className="editorial">Your entire business.</span>
              </h1>

              <p>
                {PRODUCT_NAME} connects business knowledge, customers,
                marketing, operations, analytics and supported integrations
                into one intelligent operating layer — giving your AI team the
                context to understand, recommend and help execute real work.
              </p>

              <div className="hero-actions">
                <MarketingButton>Build your AI team</MarketingButton>

                <a className="hero-secondary-button" href="#platform">
                  See how it works
                  <ChevronRight />
                </a>
              </div>

              <div className="hero-trust">
                <span>
                  <ShieldCheck />
                  Business-scoped data
                </span>

                <span>
                  <CircleCheck />
                  Governed actions
                </span>

                <span>
                  <Layers3 />
                  Multi-business ready
                </span>
              </div>
            </div>

            <div data-reveal>
              <HeroVisual />
            </div>
          </div>
        </section>

        <section className="integration-strip">
          <div className="marketing-container">
            <p>
              Designed to work across the business systems you already use
            </p>

            <div className="integration-items">
              {integrations.map(([name, Icon]) => (
                <span key={name}>
                  <Icon />
                  {name}
                </span>
              ))}
            </div>

            <small>
              Connector availability varies by provider, account configuration
              and production authorization.
            </small>
          </div>
        </section>

        <section className="platform-intro" id="platform">
          <div className="marketing-container">
            <div className="platform-heading" data-reveal>
              <SectionLabel icon={BrainCircuit}>
                The business operating layer
              </SectionLabel>

              <h2>
                Stop operating through disconnected tools.
                <br />
                <span>
                  Give the business one shared intelligence layer.
                </span>
              </h2>

              <p>
                Most businesses already have software everywhere. What they
                lack is a system that understands how everything connects.
                9D Brain brings context, intelligence, governance and execution
                into one operating model.
              </p>
            </div>

            <div className="operating-steps" data-reveal>
              {operatingSteps.map(([number, title, description]) => (
                <article key={title}>
                  <small>{number}</small>

                  <i />

                  <h3>{title}</h3>

                  <p>{description}</p>
                </article>
              ))}

              <div className="operating-rail">
                <span />
              </div>
            </div>
          </div>
        </section>

        <StorySection
          id="workforce"
          label="AI Workforce"
          icon={Bot}
          title={
            <>
              An AI team organized around{" "}
              <span className="editorial">real business roles.</span>
            </>
          }
          description="Instead of one generic assistant, 9D Brain gives different parts of the business specialized AI roles that share the same business context and governance layer."
          bullets={[
            "Business Manager coordinates priorities and the operating picture.",
            "AI CMO prepares strategy, campaigns and marketing decisions.",
            "Sales, Support, Operations and Analytics work from shared context.",
            "AI activity stays visible instead of disappearing inside isolated chats.",
          ]}
          visual={<WorkforceVisual />}
        />

        <StorySection
          id="brain"
          label="Business Brain"
          icon={BrainCircuit}
          title={
            <>
              Your business knowledge becomes{" "}
              <span className="editorial">working intelligence.</span>
            </>
          }
          description="The Business Brain is the context layer behind every AI role. It brings together what the business knows, what customers are doing and what supported connected systems are reporting."
          bullets={[
            "Brand voice, positioning, products, services and internal knowledge.",
            "Customers, CRM, conversations and operational context.",
            "Performance and provider signals remain inside the correct business boundary.",
            "The same context can support AI roles, analytics and workflows.",
          ]}
          visual={<BrainVisual />}
          reverse
        />

        <section className="operating-loop-section">
          <div className="operating-loop-glow" />

          <div className="marketing-container">
            <div className="operating-loop-copy" data-reveal>
              <SectionLabel icon={Workflow}>
                One continuous operating loop
              </SectionLabel>

              <h2>
                From business signal
                <br />
                to approved outcome.
              </h2>

              <p>
                9D Brain is designed around one repeatable operating loop rather
                than disconnected AI features.
              </p>
            </div>

            <div className="operating-loop" data-reveal>
              {[
                ["Observe", "Signals"],
                ["Understand", "Context"],
                ["Analyze", "Reasoning"],
                ["Recommend", "Decision"],
                ["Govern", "Approval"],
                ["Execute", "Action"],
                ["Measure", "Outcome"],
                ["Learn", "Improve"],
              ].map(([title, subtitle], index) => (
                <article key={title}>
                  <small>{String(index + 1).padStart(2, "0")}</small>
                  <strong>{title}</strong>
                  <span>{subtitle}</span>
                </article>
              ))}

              <div className="loop-progress">
                <i />
              </div>
            </div>
          </div>
        </section>

        <StorySection
          label="AI CMO"
          icon={Megaphone}
          title={
            <>
              Go from a growth goal to an{" "}
              <span className="editorial">approval-ready campaign.</span>
            </>
          }
          description="The AI CMO turns a business objective into strategy, creative direction, messaging, platform variants and an execution plan while keeping consequential publishing and spend behind governance."
          bullets={[
            "Start with the business goal instead of a blank prompt.",
            "Prepare brand-aware creative direction, copy, CTA and variants.",
            "Review the complete campaign before external execution.",
            "Use performance context to improve the next recommendation.",
          ]}
          visual={<CmoVisual />}
        />

        <StorySection
          label="Customers + CRM"
          icon={Users}
          title={
            <>
              One customer story across{" "}
              <span className="editorial">
                conversations and opportunities.
              </span>
            </>
          }
          description="Give your team and AI one place to understand customer history, current intent and the next useful action instead of splitting context across inboxes and separate CRM records."
          bullets={[
            "Unified customer and conversation context.",
            "Lead and opportunity workflows connected to the active business.",
            "AI-assisted prioritization grounded in stored business evidence.",
            "Industry-aware terminology and customer operations.",
          ]}
          visual={<CustomerVisual />}
          reverse
        />

        <StorySection
          id="automations"
          label="Business Autopilot"
          icon={Workflow}
          title={
            <>
              Automate the outcome.
              <br />
              <span className="editorial">Not the plumbing.</span>
            </>
          }
          description="Business owners should not need to think like automation engineers. 9D Brain is designed around business capabilities first, with deeper workflow controls available when they are genuinely useful."
          bullets={[
            "Business events can trigger AI-assisted operating workflows.",
            "AI evaluates business context before preparing a recommendation.",
            "Approval gates protect important external actions.",
            "Advanced workflow controls remain available without becoming the default UX.",
          ]}
          visual={<AutomationVisual />}
        />

        <StorySection
          label="Analytics + Intelligence"
          icon={BarChart3}
          title={
            <>
              Know what changed.
              <br />
              <span className="editorial">Know what matters next.</span>
            </>
          }
          description="Dashboards are only valuable when they improve decisions. 9D Brain combines operating analytics with AI reports, opportunity intelligence, competitor evidence and trend signals."
          bullets={[
            "Business-scoped analytics across the active operating workspace.",
            "Daily AI reporting organized around stored business evidence.",
            "Opportunity, competitor and trend intelligence.",
            "AI conclusions stay separate from the evidence that produced them.",
          ]}
          visual={<IntelligenceVisual />}
          reverse
        />

        <StorySection
          label="Website AI"
          icon={Globe2}
          title={
            <>
              Put your Business Brain{" "}
              <span className="editorial">directly on your website.</span>
            </>
          }
          description="Create a branded customer-facing AI assistant that uses the business's own knowledge, identity and supported capabilities instead of deploying another generic public chatbot."
          bullets={[
            "Use the business identity, colors and brand voice.",
            "Deploy through supported hosted and website embed flows.",
            "Customer and conversation context stays tied to the correct business.",
            "Public capabilities remain bounded instead of exposing unrestricted actions.",
          ]}
          visual={<WebsiteAiVisual />}
        />

        <section className="ecosystem-section">
          <div className="marketing-container">
            <div className="ecosystem-panel" data-reveal>
              <div className="ecosystem-copy">
                <SectionLabel icon={Network}>
                  Connected business
                </SectionLabel>

                <h2>
                  Your tools become signals.
                  <br />
                  <span className="editorial">
                    9D Brain becomes the context.
                  </span>
                </h2>

                <p>
                  Connect supported providers, select the business resources
                  you authorize and let those signals contribute to one
                  business-scoped operating picture.
                </p>

                <Link href="/register" className="ecosystem-link">
                  Build your workspace
                  <ArrowRight />
                </Link>
              </div>

              <div className="ecosystem-map">
                <svg viewBox="0 0 620 500" aria-hidden="true">
                  <path d="M310 250 L90 88" />
                  <path d="M310 250 L310 55" />
                  <path d="M310 250 L530 88" />
                  <path d="M310 250 L565 250" />
                  <path d="M310 250 L530 412" />
                  <path d="M310 250 L310 445" />
                  <path d="M310 250 L90 412" />
                  <path d="M310 250 L55 250" />
                </svg>

                <div className="ecosystem-core">
                  <ProductLogo size="lg" />
                  <strong>9D Brain</strong>
                  <span>Shared business context</span>
                </div>

                {integrations.map(([name, Icon], index) => (
                  <div
                    className={`ecosystem-item ecosystem-item-${index + 1}`}
                    key={name}
                  >
                    <Icon />
                    <span>{name}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="pricing-section" id="pricing">
          <div className="marketing-container">
            <div className="pricing-heading" data-reveal>
              <SectionLabel icon={Layers3}>
                Simple starting point
              </SectionLabel>

              <h2>
                Start with the business.
                <br />
                <span className="editorial">
                  Add more AI as you grow.
                </span>
              </h2>

              <p>
                Begin with the core operating workspace, then expand AI,
                integrations and automation capacity as the business needs it.
              </p>
            </div>

            <div className="pricing-grid" data-reveal>
              <article className="price-card">
                <div className="price-top">
                  <div>
                    <span>Free</span>
                    <h3>$0</h3>
                    <small>Start building your workspace</small>
                  </div>

                  <div className="price-icon">
                    <Layers3 />
                  </div>
                </div>

                <p>
                  Experience the core 9D Brain operating model and begin
                  building your business context.
                </p>

                <div className="price-features">
                  <FeatureBullet>Core business workspace</FeatureBullet>
                  <FeatureBullet>Business Brain foundation</FeatureBullet>
                  <FeatureBullet>CRM and operating records</FeatureBullet>
                  <FeatureBullet>Entry-level AI capacity</FeatureBullet>
                </div>

                <MarketingButton secondary>
                  Start free
                </MarketingButton>
              </article>

              <article className="price-card featured">
                <span className="popular-plan">
                  For growing businesses
                </span>

                <div className="price-top">
                  <div>
                    <span>Pro</span>
                    <h3>Pro</h3>
                    <small>More intelligence + automation</small>
                  </div>

                  <div className="price-icon">
                    <Sparkles />
                  </div>
                </div>

                <p>
                  Expand the AI workforce, integration depth, intelligence and
                  automation capacity as your operating needs grow.
                </p>

                <div className="price-features">
                  <FeatureBullet>Expanded AI workforce</FeatureBullet>
                  <FeatureBullet>Deeper integration capabilities</FeatureBullet>
                  <FeatureBullet>Advanced automation capacity</FeatureBullet>
                  <FeatureBullet>Expanded intelligence limits</FeatureBullet>
                </div>

                <MarketingButton>
                  Create your workspace
                </MarketingButton>
              </article>
            </div>

            <small className="pricing-note">
              Exact plan availability and limits are shown inside the product
              and may evolve as 9D Brain expands.
            </small>
          </div>
        </section>

        <section className="faq-section">
          <div className="marketing-container faq-layout">
            <div className="faq-heading" data-reveal>
              <SectionLabel icon={MessageCircle}>
                Questions
              </SectionLabel>

              <h2>
                Built to make AI useful for{" "}
                <span className="editorial">real business work.</span>
              </h2>

              <p>
                The operating model is deliberately different from a generic
                chatbot or low-level automation builder.
              </p>
            </div>

            <div className="faq-list" data-reveal>
              {faqs.map((faq, index) => (
                <details key={faq.title}>
                  <summary>
                    <small>
                      {String(index + 1).padStart(2, "0")}
                    </small>

                    <strong>{faq.title}</strong>

                    <i>+</i>
                  </summary>

                  <p>{faq.body}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className="final-cta-section">
          <div className="marketing-container">
            <div className="final-cta" data-reveal>
              <div className="final-grid" />
              <div className="final-orbit final-orbit-one" />
              <div className="final-orbit final-orbit-two" />

              <div className="final-logo">
                <ProductLogo size="lg" />
              </div>

              <SectionLabel icon={Zap}>
                Build your operating system
              </SectionLabel>

              <h2>
                Give your business one brain
                <br />
                and an AI team to work beside you.
              </h2>

              <p>
                Connect context, customers, operations, marketing and
                intelligence in one workspace — then turn what is happening
                into what should happen next.
              </p>

              <div className="final-actions">
                <Link href="/register" className="final-primary">
                  Start building 9D Brain
                  <ArrowRight />
                </Link>

                <Link href="/login" className="final-secondary">
                  Sign in
                </Link>
              </div>
            </div>
          </div>
        </section>
      </main>

      <footer className="marketing-footer">
        <div className="marketing-container">
          <div className="footer-grid">
            <div className="footer-brand">
              <MarketingBrand />

              <p>
                An AI operating system designed to connect business context,
                intelligence and governed execution.
              </p>
            </div>

            <div className="footer-column">
              <span>Product</span>
              <a href="#platform">Platform</a>
              <a href="#workforce">AI Workforce</a>
              <a href="#brain">Business Brain</a>
              <a href="#automations">Automations</a>
              <a href="#pricing">Pricing</a>
            </div>

            <div className="footer-column">
              <span>Account</span>
              <Link href="/register">Create account</Link>
              <Link href="/login">Sign in</Link>
            </div>

            <div className="footer-column">
              <span>Legal</span>
              <Link href="/privacy">Privacy Policy</Link>
              <Link href="/terms">Terms of Service</Link>
              <a href="mailto:hamadtheux@gmail.com">Contact</a>
            </div>
          </div>

          <div className="footer-bottom">
            <span>
              © 2026 {PRODUCT_NAME}. All rights reserved.
            </span>

            <span className="footer-status">
              <i />
              AI Business Operating System
            </span>
          </div>
        </div>
      </footer>
    </div>
  );
}
