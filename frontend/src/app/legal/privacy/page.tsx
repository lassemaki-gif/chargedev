import type { Metadata } from "next";
import Link from "next/link";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "Privacy Policy — ChargedEV",
  description:
    "How ChargedEV collects, uses and protects personal data under the GDPR.",
  alternates: { canonical: "https://chargedev.io/legal/privacy" },
};

const UPDATED = "31 October 2026";

export default function PrivacyPage() {
  return (
    <div>
      <Nav />
      <article className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-12 sm:py-16 text-ash">
        <header className="mb-10">
          <h1 className="text-3xl sm:text-4xl font-bold text-white tracking-tight mb-3">
            Privacy Policy
          </h1>
          <p className="text-sm">Last updated: {UPDATED}</p>
        </header>

        <Section n="1" title="Data controller">
          <p>
            The controller of the personal data described in this policy is
            <strong> ChargedEV.io Finland</strong> (VAT ID{" "}
            <strong>FI36604922-EX</strong>). For any question about your data,
            or to exercise the rights described in section 8, contact:
          </p>
          <p>
            <a
              href="mailto:lasse.maki@kantocompany.com"
              className="text-volt underline"
            >
              lasse.maki@kantocompany.com
            </a>
          </p>
        </Section>

        <Section n="2" title="Personal data we collect">
          <p>We collect only data we need to run the service:</p>
          <ul className="list-disc pl-5 space-y-1.5">
            <li>
              <strong>Account data</strong> — email, full name, optional
              phone number, a hashed password, email-verification state, role
              (Buyer / Host / admin).
            </li>
            <li>
              <strong>Booking data</strong> — booking IDs, listing IDs, kWh
              package, total price, VAT breakdown, PIN (shown only to the
              Buyer; stored to resolve support queries), delivery timestamps,
              status (pending / confirmed / completed), delivered kWh.
            </li>
            <li>
              <strong>Payment data</strong> — the SumUp checkout ID and
              transaction ID. ChargedEV does <strong>not</strong> see or store
              full card numbers, CVCs or 3DS tokens; those are held by our
              payment service provider SumUp.
            </li>
            <li>
              <strong>Host payout data</strong> — IBAN and account holder
              name, encrypted at rest using Fernet symmetric encryption.
              Stored only once a Host voluntarily enters it to receive
              payouts.
            </li>
            <li>
              <strong>Listing data (Hosts only)</strong> — charger title,
              address, geolocation, max power, price per kWh, availability
              schedule, optional Shelly device credentials (encrypted at rest)
              or OCPP charge-point ID.
            </li>
            <li>
              <strong>Technical data</strong> — authentication cookies, IP
              addresses in server logs for abuse prevention, and basic request
              metadata.
            </li>
          </ul>
        </Section>

        <Section n="3" title="Why we process this data and on what legal basis">
          <table className="w-full text-xs sm:text-sm border border-border rounded-lg overflow-hidden">
            <thead className="bg-card">
              <tr className="text-left text-white">
                <th className="px-3 py-2 border-b border-border">Purpose</th>
                <th className="px-3 py-2 border-b border-border">Legal basis (GDPR Art. 6)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              <tr>
                <td className="px-3 py-2">Running your account and delivering bookings</td>
                <td className="px-3 py-2">Contract performance — 6(1)(b)</td>
              </tr>
              <tr>
                <td className="px-3 py-2">Processing payments and sending receipts</td>
                <td className="px-3 py-2">Contract performance — 6(1)(b)</td>
              </tr>
              <tr>
                <td className="px-3 py-2">Finnish VAT accounting and bookkeeping</td>
                <td className="px-3 py-2">Legal obligation — 6(1)(c)</td>
              </tr>
              <tr>
                <td className="px-3 py-2">Fraud and abuse prevention, security logging</td>
                <td className="px-3 py-2">Legitimate interest — 6(1)(f)</td>
              </tr>
              <tr>
                <td className="px-3 py-2">Service emails (PIN delivery, booking confirmations)</td>
                <td className="px-3 py-2">Contract performance — 6(1)(b)</td>
              </tr>
              <tr>
                <td className="px-3 py-2">Platform improvement, anonymised analytics</td>
                <td className="px-3 py-2">Legitimate interest — 6(1)(f)</td>
              </tr>
            </tbody>
          </table>
          <p>
            We do not use personal data for automated decision-making or
            profiling with legal effects, and we do not sell personal data.
          </p>
        </Section>

        <Section n="4" title="Third parties who process data for us">
          <p>
            To run the service we use a small number of carefully chosen
            processors. All are bound by data-processing agreements and
            relevant GDPR safeguards.
          </p>
          <ul className="list-disc pl-5 space-y-1.5">
            <li>
              <strong>SumUp Limited</strong> (payment service provider,
              Ireland / UK) — card acceptance, 3DS, settlement, refunds.
            </li>
            <li>
              <strong>Railway Corporation</strong> (application hosting, USA
              with EU data residency) — runs our backend and database.
            </li>
            <li>
              <strong>Vercel Inc.</strong> (frontend hosting, USA with EU
              edge) — serves chargedev.io.
            </li>
            <li>
              <strong>Resend</strong> (transactional email) — sends PIN,
              receipt and verification emails.
            </li>
            <li>
              <strong>Google Maps Platform</strong> — geocoding of listing
              addresses so Buyers can find nearby chargers.
            </li>
          </ul>
          <p>
            For transfers outside the European Economic Area, we rely on the
            European Commission&rsquo;s{" "}
            <a
              href="https://commission.europa.eu/law/law-topic/data-protection/international-dimension-data-protection/standard-contractual-clauses-scc_en"
              target="_blank"
              rel="noopener noreferrer"
              className="text-volt underline"
            >
              Standard Contractual Clauses
            </a>
            . Where a US-based processor is certified under the EU&ndash;US
            Data Privacy Framework, we rely on that adequacy decision.
          </p>
        </Section>

        <Section n="5" title="Cookies">
          <p>
            We use a small number of first-party cookies:
          </p>
          <ul className="list-disc pl-5 space-y-1.5">
            <li>
              <strong>Authentication cookie</strong> (<code>ll_token</code>,
              HTTP-only, 1 hour) — keeps you signed in.
            </li>
            <li>
              <strong>Role cookie</strong> (<code>ll_role</code>, 1 hour) —
              tells the UI which parts of the app to show you.
            </li>
            <li>
              <strong>Local storage</strong> — remembers UI preferences such
              as the market you last visited.
            </li>
          </ul>
          <p>
            We do not use advertising cookies, cross-site tracking cookies or
            third-party analytics beyond anonymous Vercel Web Analytics.
          </p>
        </Section>

        <Section n="6" title="How long we keep data">
          <ul className="list-disc pl-5 space-y-1.5">
            <li>
              <strong>Accounting and VAT records</strong> (bookings, receipts,
              payouts): <strong>6 years</strong>, as required by the Finnish
              Accounting Act (Kirjanpitolaki).
            </li>
            <li>
              <strong>Account data</strong>: for the lifetime of your account.
              You can delete your account at any time; see section 8.
            </li>
            <li>
              <strong>Email verification tokens, password reset tokens</strong>:
              up to 7 days.
            </li>
            <li>
              <strong>Server logs</strong>: 30 days.
            </li>
          </ul>
          <p>
            When an account is deleted, we retain only the data we must keep
            for legal reasons (mainly VAT and accounting records) and we
            irreversibly disconnect it from your personal identifiers where
            that is possible.
          </p>
        </Section>

        <Section n="7" title="Where your data lives">
          <p>
            Our backend database runs on Railway infrastructure located in the
            European Union. Frontend assets are served from Vercel&rsquo;s
            global edge, with HTTPS enforced end-to-end. Payment data is held
            by SumUp in their PCI-DSS-compliant environment.
          </p>
        </Section>

        <Section n="8" title="Your rights under the GDPR">
          <p>Regardless of where you live in the EEA, you have the right to:</p>
          <ul className="list-disc pl-5 space-y-1.5">
            <li>
              <strong>Access</strong> the personal data we hold about you (Art. 15).
            </li>
            <li>
              <strong>Correct</strong> inaccurate or incomplete data (Art. 16).
            </li>
            <li>
              <strong>Erase</strong> your data, except where we must keep it
              for legal reasons such as VAT records (Art. 17).
            </li>
            <li>
              <strong>Restrict</strong> processing while a dispute or
              correction is pending (Art. 18).
            </li>
            <li>
              <strong>Receive a portable copy</strong> of the data you
              provided to us in a structured, machine-readable format (Art. 20).
            </li>
            <li>
              <strong>Object</strong> to processing based on our legitimate
              interests (Art. 21).
            </li>
            <li>
              <strong>Lodge a complaint</strong> with the Finnish Data
              Protection Ombudsman (
              <a
                href="https://tietosuoja.fi"
                target="_blank"
                rel="noopener noreferrer"
                className="text-volt underline"
              >
                tietosuoja.fi
              </a>
              ) or your local supervisory authority.
            </li>
          </ul>
          <p>
            To exercise any of these rights, email{" "}
            <a
              href="mailto:lasse.maki@kantocompany.com"
              className="text-volt underline"
            >
              lasse.maki@kantocompany.com
            </a>
            . We respond within 30 days.
          </p>
        </Section>

        <Section n="9" title="How we secure your data">
          <p>
            Passwords are stored only as salted hashes. IBANs and host-held
            device credentials are encrypted at rest. Transport is HTTPS only.
            Access to production data is limited to the people who need it to
            run the service. We log and alert on anomalous access.
          </p>
        </Section>

        <Section n="10" title="Children">
          <p>
            ChargedEV is not directed at children under 18. We do not
            knowingly collect personal data from anyone under 18. If you
            believe a child has created an account, write to us at the address
            above and we will delete it.
          </p>
        </Section>

        <Section n="11" title="Changes to this policy">
          <p>
            We may update this policy to reflect changes in the service, in
            our processors, or in the law. Material changes will be
            communicated by email to the address on your account at least 14
            days before they take effect.
          </p>
        </Section>

        <nav className="mt-16 flex gap-4 text-sm">
          <Link href="/legal/terms" className="text-volt underline">
            Terms and Conditions →
          </Link>
          <Link href="/" className="text-ash hover:text-white">
            ← Home
          </Link>
        </nav>
      </article>
    </div>
  );
}

function Section({
  n,
  title,
  children,
}: {
  n: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-10">
      <h2 className="text-xl font-semibold text-white mb-3">
        {n}. {title}
      </h2>
      <div className="space-y-3 text-sm sm:text-[15px] leading-relaxed">
        {children}
      </div>
    </section>
  );
}
