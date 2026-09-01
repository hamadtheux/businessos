import { useEffect, type CSSProperties, type ReactNode } from "react";
import { Link } from "wouter";
import { ProductLogo } from "@/components/product-brand";
import { PRODUCT_NAME } from "@/config/brand";
import { MarketingHomePage } from "./public-home";

const SUPPORT_EMAIL = "hamadtheux@gmail.com";
const EFFECTIVE_DATE = "August 31, 2026";

const styles: Record<string, CSSProperties> = {
  page: {
    minHeight: "100dvh",
    background:
      "radial-gradient(circle at top left, rgba(29,134,58,0.10), transparent 34%), #f7faf8",
    color: "#102217",
  },
  header: {
    position: "sticky",
    top: 0,
    zIndex: 20,
    backdropFilter: "blur(18px)",
    background: "rgba(247,250,248,0.88)",
    borderBottom: "1px solid rgba(16,34,23,0.08)",
  },
  headerInner: {
    width: "min(1120px, calc(100% - 40px))",
    margin: "0 auto",
    minHeight: 72,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 24,
  },
  brand: {
    display: "inline-flex",
    alignItems: "center",
    gap: 10,
    color: "#102217",
    textDecoration: "none",
    fontSize: 17,
    fontWeight: 800,
    letterSpacing: "-0.02em",
  },
  nav: {
    display: "flex",
    alignItems: "center",
    gap: 18,
    flexWrap: "wrap",
  },
  navLink: {
    color: "#405247",
    textDecoration: "none",
    fontSize: 14,
    fontWeight: 650,
  },
  button: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    minHeight: 42,
    padding: "0 18px",
    borderRadius: 12,
    background: "#1D863A",
    color: "#fff",
    textDecoration: "none",
    fontSize: 14,
    fontWeight: 750,
    boxShadow: "0 8px 24px rgba(29,134,58,0.18)",
  },
  secondaryButton: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    minHeight: 42,
    padding: "0 18px",
    borderRadius: 12,
    border: "1px solid rgba(16,34,23,0.12)",
    background: "#fff",
    color: "#17301f",
    textDecoration: "none",
    fontSize: 14,
    fontWeight: 700,
  },
  main: {
    width: "min(1120px, calc(100% - 40px))",
    margin: "0 auto",
  },
  hero: {
    padding: "96px 0 72px",
    maxWidth: 880,
  },
  eyebrow: {
    display: "inline-flex",
    border: "1px solid rgba(29,134,58,0.18)",
    background: "rgba(255,255,255,0.78)",
    color: "#176B31",
    borderRadius: 999,
    padding: "8px 12px",
    fontSize: 13,
    fontWeight: 750,
    marginBottom: 22,
  },
  heroTitle: {
    margin: 0,
    maxWidth: 850,
    fontSize: "clamp(48px, 7vw, 84px)",
    lineHeight: 0.98,
    letterSpacing: "-0.055em",
    fontWeight: 850,
  },
  heroText: {
    margin: "28px 0 0",
    maxWidth: 720,
    color: "#526258",
    fontSize: "clamp(17px, 2vw, 21px)",
    lineHeight: 1.65,
  },
  actions: {
    display: "flex",
    gap: 12,
    flexWrap: "wrap",
    marginTop: 32,
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
    gap: 16,
    paddingBottom: 96,
  },
  card: {
    padding: 28,
    borderRadius: 22,
    background: "rgba(255,255,255,0.88)",
    border: "1px solid rgba(16,34,23,0.08)",
    boxShadow: "0 20px 60px rgba(16,34,23,0.05)",
  },
  cardNumber: {
    width: 34,
    height: 34,
    display: "grid",
    placeItems: "center",
    borderRadius: 10,
    background: "#ECFAA2",
    color: "#115127",
    fontSize: 13,
    fontWeight: 850,
    marginBottom: 26,
  },
  cardTitle: {
    margin: 0,
    fontSize: 20,
    letterSpacing: "-0.025em",
  },
  cardText: {
    margin: "10px 0 0",
    color: "#657269",
    lineHeight: 1.65,
    fontSize: 15,
  },
  legalWrap: {
    width: "min(860px, calc(100% - 40px))",
    margin: "0 auto",
    padding: "72px 0 100px",
  },
  legalIntro: {
    marginBottom: 44,
  },
  legalTitle: {
    margin: 0,
    fontSize: "clamp(40px, 6vw, 64px)",
    lineHeight: 1,
    letterSpacing: "-0.045em",
  },
  legalLead: {
    margin: "18px 0 0",
    color: "#637067",
    fontSize: 17,
    lineHeight: 1.7,
  },
  legalCard: {
    background: "#fff",
    border: "1px solid rgba(16,34,23,0.08)",
    borderRadius: 24,
    padding: "clamp(24px, 5vw, 48px)",
    boxShadow: "0 24px 70px rgba(16,34,23,0.05)",
  },
  section: {
    padding: "28px 0",
    borderBottom: "1px solid rgba(16,34,23,0.08)",
  },
  sectionTitle: {
    margin: "0 0 12px",
    fontSize: 22,
    letterSpacing: "-0.025em",
  },
  paragraph: {
    margin: "10px 0",
    color: "#506057",
    fontSize: 15.5,
    lineHeight: 1.75,
  },
  list: {
    margin: "12px 0 0",
    paddingLeft: 22,
    color: "#506057",
    fontSize: 15.5,
    lineHeight: 1.75,
  },
  notice: {
    marginTop: 18,
    borderRadius: 16,
    background: "#f0f8f2",
    border: "1px solid rgba(29,134,58,0.14)",
    padding: 18,
    color: "#23492e",
    fontSize: 14.5,
    lineHeight: 1.7,
  },
  footer: {
    borderTop: "1px solid rgba(16,34,23,0.08)",
    padding: "28px 0 38px",
  },
  footerInner: {
    width: "min(1120px, calc(100% - 40px))",
    margin: "0 auto",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 20,
    flexWrap: "wrap",
    color: "#758078",
    fontSize: 13,
  },
  footerLinks: {
    display: "flex",
    gap: 18,
    flexWrap: "wrap",
  },
  relatedLinks: {
    display: "flex",
    gap: 18,
    flexWrap: "wrap",
    paddingTop: 28,
    fontSize: 15,
    fontWeight: 650,
  },
};

function PageMetadata({
  title,
  description,
  canonicalPath,
}: {
  title: string;
  description: string;
  canonicalPath: string;
}) {
  useEffect(() => {
    const previousTitle = document.title;
    const metadata = [
      ["meta[name='description']", "content", description],
      ["meta[property='og:title']", "content", title],
      ["meta[property='og:description']", "content", description],
      ["meta[property='og:url']", "content", `https://9dbrain.com${canonicalPath}`],
      ["meta[name='twitter:title']", "content", title],
      ["meta[name='twitter:description']", "content", description],
      ["link[rel='canonical']", "href", `https://9dbrain.com${canonicalPath}`],
    ] as const;
    const previousValues = metadata.map(([selector, attribute]) => {
      const element = document.head.querySelector(selector);
      return { element, attribute, value: element?.getAttribute(attribute) };
    });

    document.title = title;
    metadata.forEach(([selector, attribute, value]) => {
      document.head.querySelector(selector)?.setAttribute(attribute, value);
    });

    return () => {
      document.title = previousTitle;
      previousValues.forEach(({ element, attribute, value }) => {
        if (value === null || value === undefined) {
          element?.removeAttribute(attribute);
        } else {
          element?.setAttribute(attribute, value);
        }
      });
    };
  }, [canonicalPath, description, title]);

  return null;
}

function PublicHeader() {
  return (
    <header style={styles.header}>
      <div style={styles.headerInner}>
        <Link href="/" style={styles.brand}>
          <ProductLogo size="sm" decorative />
          <span>{PRODUCT_NAME}</span>
        </Link>

        <nav style={styles.nav} aria-label="Public navigation">
          <Link href="/privacy" style={styles.navLink}>
            Privacy
          </Link>
          <Link href="/terms" style={styles.navLink}>
            Terms
          </Link>
          <Link href="/login" style={styles.secondaryButton}>
            Sign in
          </Link>
        </nav>
      </div>
    </header>
  );
}

function PublicFooter() {
  return (
    <footer style={styles.footer}>
      <div style={styles.footerInner}>
        <span>© 2026 {PRODUCT_NAME}. AI-powered business operations.</span>
        <div style={styles.footerLinks}>
          <Link href="/privacy" style={styles.navLink}>
            Privacy Policy
          </Link>
          <Link href="/terms" style={styles.navLink}>
            Terms of Service
          </Link>
          <Link href="/data-deletion" style={styles.navLink}>
            Data Deletion
          </Link>
          <a href={`mailto:${SUPPORT_EMAIL}`} style={styles.navLink}>
            Contact
          </a>
        </div>
      </div>
    </footer>
  );
}

function PublicShell({ children }: { children: ReactNode }) {
  return (
    <div style={styles.page}>
      <PublicHeader />
      {children}
      <PublicFooter />
    </div>
  );
}

function LegalSection({
  title,
  children,
  last = false,
}: {
  title: string;
  children: ReactNode;
  last?: boolean;
}) {
  return (
    <section
      style={{
        ...styles.section,
        ...(last ? { borderBottom: "none", paddingBottom: 0 } : {}),
      }}
    >
      <h2 style={styles.sectionTitle}>{title}</h2>
      {children}
    </section>
  );
}

export function PublicHomePage() {
  return <MarketingHomePage />;
}


export function DataDeletionPage() {
  return (
    <PublicShell>
      <PageMetadata
        title="Data Deletion Instructions | 9D Brain"
        description="Learn how to request deletion of your personal data and connected Meta integration data from 9D Brain."
        canonicalPath="/data-deletion"
      />
      <main style={styles.legalWrap}>
        <div style={styles.legalIntro}>
          <span style={styles.eyebrow}>Legal & privacy</span>
          <h1 style={styles.legalTitle}>Data Deletion Instructions</h1>
          <p style={styles.legalLead}>
            {PRODUCT_NAME} respects your privacy and gives you the ability to
            request deletion of personal data associated with your account and
            connected integrations.
          </p>
        </div>

        <article style={styles.legalCard}>
          <LegalSection title="How to Request Data Deletion">
            <p style={styles.paragraph}>
              Users can request deletion of their data in either of the
              following ways:
            </p>
            <ol style={styles.list}>
              <li>
                <strong>From {PRODUCT_NAME}</strong>
                <p style={styles.paragraph}>
                  Sign in to your {PRODUCT_NAME} account and go to: Settings →
                  Privacy / Account → Delete Account or Request Data Deletion.
                </p>
                <p style={styles.paragraph}>
                  If this feature is not currently available in the UI, submit
                  a deletion request by email using the method below.
                </p>
              </li>
              <li>
                <strong>By Email</strong>
                <p style={styles.paragraph}>
                  Send a data deletion request to{" "}
                  <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>.
                </p>
                <p style={styles.paragraph}>
                  Include the email address associated with your {PRODUCT_NAME}
                  account so the request can be verified.
                </p>
              </li>
            </ol>
          </LegalSection>

          <LegalSection title="Meta / Facebook Data">
            <p style={styles.paragraph}>
              If you have connected Facebook, Instagram, Messenger, Meta Ads,
              Pages, Leads, Catalogs, or another Meta service to {PRODUCT_NAME},
              you may disconnect the integration from your {PRODUCT_NAME}
              account.
            </p>
            <p style={styles.paragraph}>
              When a valid deletion request is received, {PRODUCT_NAME} will
              delete or anonymize personal data associated with you as
              required, including applicable data obtained through Meta APIs,
              subject to legitimate legal, security, fraud-prevention,
              accounting, or regulatory retention requirements.
            </p>
          </LegalSection>

          <LegalSection title="Processing Time">
            <p style={styles.paragraph}>
              Verified deletion requests will be processed within a reasonable
              period and in accordance with applicable privacy laws.
            </p>
            <p style={styles.paragraph}>
              Where applicable, you will receive confirmation when the
              deletion request has been completed.
            </p>
          </LegalSection>

          <LegalSection title="Information We May Retain">
            <p style={styles.paragraph}>
              Certain information may be retained when necessary for:
            </p>
            <ul style={styles.list}>
              <li>Legal or regulatory obligations</li>
              <li>Fraud and abuse prevention</li>
              <li>Security and audit records</li>
              <li>Billing and financial record requirements</li>
              <li>Resolving disputes</li>
              <li>Enforcing agreements</li>
            </ul>
            <p style={styles.paragraph}>
              Any retained information will remain protected and will not be
              used for unrelated purposes.
            </p>
          </LegalSection>

          <LegalSection title="Contact" last>
            <p style={styles.paragraph}>
              For questions or requests regarding deletion of personal data,
              contact {PRODUCT_NAME} at{" "}
              <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>.
            </p>
            <nav
              aria-label="Related legal information"
              style={styles.relatedLinks}
            >
              <Link href="/privacy">Privacy Policy</Link>
              <Link href="/terms">Terms of Service</Link>
            </nav>
          </LegalSection>
        </article>
      </main>
    </PublicShell>
  );
}


export function PrivacyPolicyPage() {
  return (
    <PublicShell>
      <main style={styles.legalWrap}>
        <div style={styles.legalIntro}>
          <span style={styles.eyebrow}>Legal & privacy</span>
          <h1 style={styles.legalTitle}>Privacy Policy</h1>
          <p style={styles.legalLead}>
            Effective {EFFECTIVE_DATE}. This policy explains how {PRODUCT_NAME}
            collects, uses, stores, protects, and shares information when you
            use our services and connected integrations.
          </p>
        </div>

        <article style={styles.legalCard}>
          <LegalSection title="1. Information we collect">
            <p style={styles.paragraph}>
              We may collect account information, business workspace data,
              configuration choices, customer and operational information you
              provide, technical and security logs, and information received
              from third-party services that you explicitly connect.
            </p>
            <p style={styles.paragraph}>
              For Google integrations, the information available to
              {` ${PRODUCT_NAME} `} depends on the specific permissions you
              authorize. This can include selected Gmail information, Calendar
              data, Drive information, Google Ads information, and basic Google
              account identity information when required to provide the
              integration you selected.
            </p>
          </LegalSection>

          <LegalSection title="2. How we use information">
            <p style={styles.paragraph}>
              We use information to provide and secure the service, maintain
              your business workspace, operate requested integrations,
              synchronize authorized data, support user-facing automation and
              AI-assisted features, provide reporting and analytics, prevent
              abuse, troubleshoot problems, and improve reliability.
            </p>
            <p style={styles.paragraph}>
              We do not sell personal information or Google user data. We do
              not use Google Workspace API data for advertising, retargeting,
              credit decisions, or to train generalized artificial
              intelligence or machine-learning models.
            </p>
          </LegalSection>

          <LegalSection title="3. Google API data and Limited Use">
            <p style={styles.paragraph}>
              Google connections are initiated by the user. We request access
              only to permissions needed for the integration and features the
              user chooses to use. Users remain in control of whether a Google
              account is connected and may revoke access.
            </p>
            <div style={styles.notice}>
              {PRODUCT_NAME}&apos;s use of information received from Google APIs
              will adhere to the Google API Services User Data Policy,
              including the Limited Use requirements.
            </div>
            <p style={styles.paragraph}>
              Google user data is used only to provide or improve user-facing
              features associated with the authorized business connection.
              Where an AI-assisted feature processes connected data, that
              processing is limited to delivering the user-requested feature
              and remains subject to these data-use restrictions.
            </p>
          </LegalSection>

          <LegalSection title="4. Integration credentials and security">
            <p style={styles.paragraph}>
              OAuth access and refresh credentials for supported integrations
              are stored in a dedicated secure credential store rather than as
              ordinary application data. Credential references are bound to
              the relevant business and connector so that one business cannot
              use another business&apos;s integration credentials.
            </p>
            <p style={styles.paragraph}>
              We use administrative, technical, and organizational safeguards
              designed to protect information against unauthorized access,
              alteration, disclosure, or destruction. No system can guarantee
              absolute security, and users should also protect their account
              credentials and devices.
            </p>
          </LegalSection>

          <LegalSection title="5. How information may be shared">
            <p style={styles.paragraph}>
              We may share information with infrastructure and service
              providers only when reasonably necessary to operate,
              secure, or provide requested user-facing features. We may also
              disclose information where required by law, to protect rights and
              security, or with the user&apos;s direction or consent.
            </p>
            <p style={styles.paragraph}>
              Connected providers such as Google independently process
              information under their own terms and privacy policies when you
              interact with their services.
            </p>
          </LegalSection>

          <LegalSection title="6. Data retention and deletion">
            <p style={styles.paragraph}>
              We retain information for as long as reasonably necessary to
              provide the service, maintain security and auditability, satisfy
              legitimate business requirements, and comply with applicable
              legal obligations.
            </p>
            <p style={styles.paragraph}>
              Users can disconnect integrations to stop future access from that
              connection. You may also contact us to request deletion of
              account or service data, subject to legal, security, fraud
              prevention, backup, and contractual retention requirements.
            </p>
          </LegalSection>

          <LegalSection title="7. Your choices">
            <ul style={styles.list}>
              <li>Choose which supported third-party services to connect.</li>
              <li>Review the permissions requested during provider authorization.</li>
              <li>Disconnect or revoke connected provider access.</li>
              <li>Request access, correction, or deletion where applicable.</li>
              <li>Contact us with privacy or data-use questions.</li>
            </ul>
          </LegalSection>

          <LegalSection title="8. Changes to this policy">
            <p style={styles.paragraph}>
              We may update this policy as the service, integrations, or legal
              requirements change. Material changes will be reflected by an
              updated effective date and, when appropriate, additional notice
              or consent before information is used for a materially different
              purpose.
            </p>
          </LegalSection>

          <LegalSection title="9. Contact" last>
            <p style={styles.paragraph}>
              Questions about this Privacy Policy or your data can be sent to{" "}
              <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>.
            </p>
          </LegalSection>
        </article>
      </main>
    </PublicShell>
  );
}

export function TermsOfServicePage() {
  return (
    <PublicShell>
      <main style={styles.legalWrap}>
        <div style={styles.legalIntro}>
          <span style={styles.eyebrow}>Legal & service terms</span>
          <h1 style={styles.legalTitle}>Terms of Service</h1>
          <p style={styles.legalLead}>
            Effective {EFFECTIVE_DATE}. These Terms govern access to and use of
            {` ${PRODUCT_NAME}`}.
          </p>
        </div>

        <article style={styles.legalCard}>
          <LegalSection title="1. The service">
            <p style={styles.paragraph}>
              {PRODUCT_NAME} provides business software for managing business
              context, operations, integrations, workflows, analytics,
              AI-assisted recommendations, and governed actions. Features may
              vary by plan, configuration, provider availability, and business
              type.
            </p>
          </LegalSection>

          <LegalSection title="2. Accounts and businesses">
            <p style={styles.paragraph}>
              You are responsible for maintaining accurate account
              information, protecting account credentials, and ensuring that
              people you authorize to access a business workspace have
              appropriate permission to do so.
            </p>
            <p style={styles.paragraph}>
              Each business workspace is treated as an independent business
              context. You are responsible for ensuring that information and
              connected accounts added to a workspace belong to that business
              or are used with appropriate authorization.
            </p>
          </LegalSection>

          <LegalSection title="3. Connected services">
            <p style={styles.paragraph}>
              You may choose to connect supported third-party services. By
              connecting a provider, you authorize {PRODUCT_NAME} to use the
              permissions you approve for the purpose of providing the
              selected integration features.
            </p>
            <p style={styles.paragraph}>
              Third-party services remain governed by their own terms,
              policies, availability, quotas, approvals, and technical
              requirements. We do not control those services and cannot
              guarantee that a provider will approve or continuously maintain
              an integration.
            </p>
          </LegalSection>

          <LegalSection title="4. AI-assisted features and approvals">
            <p style={styles.paragraph}>
              AI-assisted outputs can contain mistakes and should be evaluated
              based on the importance and risk of the decision. Where the
              service presents an approval or confirmation step, you are
              responsible for reviewing the proposed action before approving
              it.
            </p>
            <p style={styles.paragraph}>
              You should not rely on the service as a substitute for
              professional legal, financial, medical, tax, regulatory, or
              other licensed professional advice.
            </p>
          </LegalSection>

          <LegalSection title="5. Acceptable use">
            <p style={styles.paragraph}>
              You may not use the service to violate law, infringe rights,
              obtain unauthorized access, distribute malware, abuse connected
              providers, bypass security controls, misrepresent identity or
              authorization, or process data that you do not have the right to
              use.
            </p>
          </LegalSection>

          <LegalSection title="6. Your data">
            <p style={styles.paragraph}>
              You retain your rights in information you submit to the service.
              You grant us the limited rights necessary to host, process,
              transmit, and otherwise handle that information to provide,
              secure, and maintain the service and the features you request.
            </p>
            <p style={styles.paragraph}>
              Our handling of personal information and Google API data is
              described in the{" "}
              <Link href="/privacy">Privacy Policy</Link>.
            </p>
          </LegalSection>

          <LegalSection title="7. Plans, fees, and changes">
            <p style={styles.paragraph}>
              Paid features may be subject to subscription fees, usage limits,
              billing periods, taxes, and plan-specific entitlements presented
              at purchase or in the product. We may change plans or features
              prospectively and will provide notice where required.
            </p>
          </LegalSection>

          <LegalSection title="8. Availability and service changes">
            <p style={styles.paragraph}>
              We work to provide a reliable service, but uninterrupted or
              error-free availability is not guaranteed. We may update,
              suspend, limit, or discontinue functionality where reasonably
              necessary for security, maintenance, provider changes,
              compliance, or product development.
            </p>
          </LegalSection>

          <LegalSection title="9. Suspension and termination">
            <p style={styles.paragraph}>
              Access may be limited or suspended when reasonably necessary to
              protect users or the service, respond to unlawful or abusive
              activity, address security risks, enforce these Terms, or comply
              with legal obligations. Users may stop using the service at any
              time, subject to applicable subscription commitments.
            </p>
          </LegalSection>

          <LegalSection title="10. Disclaimers and responsibility">
            <p style={styles.paragraph}>
              To the maximum extent permitted by applicable law, the service is
              provided on an as-available basis. You remain responsible for
              business decisions, approved actions, the accuracy and legality
              of data you provide, and compliance obligations applicable to
              your business.
            </p>
          </LegalSection>

          <LegalSection title="11. Changes to these Terms">
            <p style={styles.paragraph}>
              We may update these Terms as the service evolves. Material
              changes will be reflected by an updated effective date and
              additional notice where appropriate or legally required.
            </p>
          </LegalSection>

          <LegalSection title="12. Contact" last>
            <p style={styles.paragraph}>
              Questions about these Terms can be sent to{" "}
              <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>.
            </p>
          </LegalSection>
        </article>
      </main>
    </PublicShell>
  );
}
