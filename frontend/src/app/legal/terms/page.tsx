import type { Metadata } from "next";
import Link from "next/link";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "Terms and Conditions — ChargedEV",
  description:
    "Terms governing the use of the ChargedEV peer-to-peer EV charging marketplace.",
  alternates: { canonical: "https://chargedev.io/legal/terms" },
};

const UPDATED = "31 October 2026";

export default function TermsPage() {
  return (
    <div>
      <Nav />
      <article className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-12 sm:py-16 text-ash">
        <header className="mb-10">
          <h1 className="text-3xl sm:text-4xl font-bold text-white tracking-tight mb-3">
            Terms and Conditions
          </h1>
          <p className="text-sm">Last updated: {UPDATED}</p>
        </header>

        <Section n="1" title="Who we are">
          <p>
            These Terms and Conditions (&ldquo;Terms&rdquo;) govern your use of
            the ChargedEV marketplace at <strong>chargedev.io</strong>. The
            service is operated by <strong>ChargedEV.io Finland</strong>, a
            business registered in Finland, VAT ID{" "}
            <strong>FI36604922-EX</strong> (&ldquo;ChargedEV&rdquo;,
            &ldquo;we&rdquo;, &ldquo;us&rdquo;).
          </p>
          <p>
            By creating an account, making a booking or offering a charging
            point for rent through ChargedEV, you agree to be bound by these
            Terms and by our{" "}
            <Link href="/legal/privacy" className="text-volt underline">
              Privacy Policy
            </Link>
            . If you do not accept them, do not use the service.
          </p>
        </Section>

        <Section n="2" title="What ChargedEV is">
          <p>
            ChargedEV is an online marketplace that matches EV drivers
            (&ldquo;Buyers&rdquo;) with homeowners and small businesses whose
            installed EV charging point has spare availability
            (&ldquo;Hosts&rdquo;). We do not own or operate any charging
            equipment and are not an energy utility.
          </p>
          <p>
            For every Buyer transaction, ChargedEV acts as the{" "}
            <strong>deemed supplier for Finnish VAT purposes</strong> (currently
            25.5%). We charge the Buyer, remit the VAT to Verohallinto, retain
            a platform fee, and pay the balance to the Host under a commercial
            agency relationship.
          </p>
        </Section>

        <Section n="3" title="Account registration">
          <p>
            You must be at least 18 years old and legally capable of entering
            into a contract. You must provide accurate information, keep your
            password confidential, and tell us promptly if you suspect your
            account has been compromised. Each person may hold one Buyer
            account and, if applicable, one Host account.
          </p>
          <p>
            We may suspend or close any account that violates these Terms,
            contains false information, or is being used to abuse the service.
          </p>
        </Section>

        <Section n="4" title="Charging packages, pricing and limits">
          <ul className="list-disc pl-5 space-y-1.5">
            <li>
              Charging is sold in fixed packages of 20, 40, 60 or 80 kWh per
              session.
            </li>
            <li>
              Host prices are capped at <strong>€0.95 per kWh including VAT</strong>.
              A single session cannot exceed <strong>80 kWh</strong> or
              <strong> €76</strong> gross.
            </li>
            <li>
              The displayed total is the final amount the Buyer pays. It
              includes the Host&rsquo;s share, the ChargedEV platform fee and
              Finnish VAT.
            </li>
            <li>
              Payment is taken in full at the time of booking. No payment, no
              PIN, no charge.
            </li>
          </ul>
        </Section>

        <Section n="5" title="Payment, receipts and the PIN">
          <p>
            All payments are processed by <strong>SumUp Limited</strong>, our
            payment service provider. Accepted methods include Visa,
            Mastercard, American Express, Apple Pay and Google Pay. ChargedEV
            does not see or store full card details.
          </p>
          <p>
            On successful payment the Buyer receives a unique 6-digit PIN,
            delivered on-screen and by email. The PIN authorises exactly one
            charging session for the booked kWh amount. Share it only with the
            Host (or enter it at the charger) at the start of the session.
          </p>
          <p>
            A VAT-compliant receipt is available in the Buyer&rsquo;s
            &ldquo;My bookings&rdquo; page.
          </p>
        </Section>

        <Section n="6" title="How the charging session works">
          <p>
            The charging point is operated by the Host. Sessions use open
            metering (OCPP 1.6-J or Shelly cloud-metered sockets) so delivered
            energy is logged to the booking in real time. The session ends
            automatically when the booked kWh is reached, when a safety fault
            is detected, or if the vehicle disconnects.
          </p>
          <p>
            Buyers must follow the Host&rsquo;s access instructions, treat
            equipment with reasonable care, and leave the charging area as they
            found it.
          </p>
        </Section>

        <Section n="7" title="Host obligations and safety">
          <p>
            Hosts are solely responsible for the condition, installation and
            legal compliance of their charging equipment. Specifically:
          </p>
          <ul className="list-disc pl-5 space-y-1.5">
            <li>
              Wiring, breakers and residual-current protection must be
              installed by a <strong>certified electrician</strong> and comply
              with Finnish electrical safety regulations and manufacturer
              requirements.
            </li>
            <li>
              The equipment must be in a safe, working condition at the time
              of every booking. Hosts must disable their listing if the
              equipment is damaged or unavailable.
            </li>
            <li>
              Hosts carry appropriate household or business insurance covering
              their charging equipment and the surrounding premises.
            </li>
            <li>
              Hosts verify Buyer PINs before enabling a session and must not
              deliver energy without a valid PIN.
            </li>
          </ul>
        </Section>

        <Section n="8" title="Host joining fee and payouts">
          <p>
            New Hosts pay a one-time <strong>€1 refundable verification
            charge</strong> to activate their account. The €1 is refunded
            automatically to the paying card after the Host&rsquo;s first
            completed booking.
          </p>
          <p>
            For each completed booking, ChargedEV retains a platform fee of{" "}
            <strong>20% of the net amount</strong> (net = total minus Finnish
            VAT). The remainder is the Host&rsquo;s earnings and is paid to the
            IBAN on file by weekly SEPA transfer from ChargedEV&rsquo;s bank.
            Payouts require that the Host has provided a valid IBAN; otherwise
            earnings are held until an IBAN is supplied.
          </p>
        </Section>

        <Section n="9" title="Cancellations and refunds">
          <p>
            Because a Buyer&rsquo;s PIN authorises an on-demand service that
            consumes a limited, time-bound resource (the Host&rsquo;s charging
            point and electricity), the right of withdrawal under the Finnish
            Consumer Protection Act (Chapter 6, section 16, point 1) does not
            apply once charging has started.
          </p>
          <p>Before charging starts:</p>
          <ul className="list-disc pl-5 space-y-1.5">
            <li>
              If the Buyer cancels within 30 minutes of booking and before
              arriving at the charging point, we refund the full amount.
            </li>
            <li>
              If the Host cannot deliver the booked session (equipment fault,
              access denied, over-booking), the Buyer is refunded in full and
              the Host receives no earnings for that booking.
            </li>
            <li>
              If delivered energy is materially less than booked (more than a
              1 kWh shortfall), the Buyer is refunded for the undelivered
              portion.
            </li>
          </ul>
          <p>
            Refund requests go to{" "}
            <a href="mailto:support@chargedev.io" className="text-volt underline">
              support@chargedev.io
            </a>{" "}
            with the booking ID.
          </p>
        </Section>

        <Section n="10" title="Disputes between Buyers and Hosts">
          <p>
            ChargedEV is the merchant of record for every Buyer transaction.
            Chargebacks, refund requests and complaints are handled by
            ChargedEV; Buyers do not contact Hosts directly to dispute a
            payment. We may deduct the disputed amount from the Host&rsquo;s
            next payout where the dispute is resolved in the Buyer&rsquo;s
            favour.
          </p>
        </Section>

        <Section n="11" title="Prohibited uses">
          <p>You agree not to:</p>
          <ul className="list-disc pl-5 space-y-1.5">
            <li>
              use the service for anything unlawful or in breach of consumer,
              energy or electrical safety law;
            </li>
            <li>
              list a charging point you are not entitled to rent out (for
              example, a tenant without the landlord&rsquo;s permission);
            </li>
            <li>
              resell wholesale electricity, operate as a public charging point
              operator (CPO) without the required licences, or circumvent the
              €0.95/kWh cap;
            </li>
            <li>
              share, resell or publish PINs; attempt to extract energy beyond
              the booked amount; or tamper with metering;
            </li>
            <li>
              scrape or copy data from the platform except as permitted by
              law.
            </li>
          </ul>
        </Section>

        <Section n="12" title="Our liability">
          <p>
            ChargedEV provides the marketplace software and acts as merchant
            of record. We are not liable for the physical condition of a
            Host&rsquo;s equipment, damage caused by faulty wiring or
            installation, loss of vehicle warranty, or any indirect or
            consequential losses.
          </p>
          <p>
            Our aggregate liability to a Buyer in connection with a single
            booking is capped at <strong>the total paid for that
            booking</strong>. Nothing in these Terms limits liability that
            cannot be limited under Finnish law — in particular, liability for
            death, personal injury caused by negligence, or fraud.
          </p>
        </Section>

        <Section n="13" title="Service availability">
          <p>
            We aim to keep the service available at all times but do not
            guarantee uninterrupted operation. We may suspend parts of the
            service for maintenance, security or legal reasons without prior
            notice.
          </p>
        </Section>

        <Section n="14" title="Changes to these Terms">
          <p>
            We may update these Terms from time to time. Material changes will
            be communicated by email to the address on your account at least
            14 days before they take effect. Continued use of the service
            after that date constitutes acceptance.
          </p>
        </Section>

        <Section n="15" title="Governing law and disputes">
          <p>
            These Terms are governed by Finnish law. Consumer Buyers may bring
            proceedings in their local court. Non-consumer disputes are heard
            exclusively by the district court of Helsinki.
          </p>
          <p>
            Consumer Buyers may also use the European Commission&rsquo;s{" "}
            <a
              href="https://ec.europa.eu/consumers/odr/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-volt underline"
            >
              Online Dispute Resolution platform
            </a>{" "}
            or the Finnish Consumer Disputes Board (Kuluttajariitalautakunta).
            We are willing to engage with those bodies in good faith.
          </p>
        </Section>

        <Section n="16" title="Contact">
          <p>
            General questions, legal and privacy:{" "}
            <a href="mailto:support@chargedev.io" className="text-volt underline">
              support@chargedev.io
            </a>
          </p>
        </Section>

        <nav className="mt-16 flex gap-4 text-sm">
          <Link href="/legal/privacy" className="text-volt underline">
            Privacy Policy →
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
