import { streamText, stepCountIs } from 'ai';
import type { ModelMessage } from 'ai';
import { anthropic } from '@ai-sdk/anthropic';
import { createStripeAgentToolkit } from '@stripe/agent-toolkit/ai-sdk';
import type { StripeAgentToolkit } from '@stripe/agent-toolkit/ai-sdk';

export const maxDuration = 60;

// Cache the initialized toolkit across warm invocations
let toolkitPromise: Promise<StripeAgentToolkit> | null = null;

function getToolkit(): Promise<StripeAgentToolkit> {
  if (!toolkitPromise) {
    toolkitPromise = createStripeAgentToolkit({
      secretKey: process.env.STRIPE_SECRET_KEY!,
      configuration: {},
    });
  }
  return toolkitPromise;
}

const SYSTEM = `You are a Stripe operations assistant for ChargedEV, a peer-to-peer EV charging marketplace.

Platform context:
- Buyers pay via Stripe Checkout one-time sessions in EUR
- ChargedEV takes a 20% platform fee; charger hosts receive 80% via Stripe Connect transfers
- Sellers use Stripe Express accounts for payouts
- All monetary values are in EUR

Keep answers concise and data-focused. Format monetary amounts with € and two decimal places.`;

export async function POST(req: Request) {
  const { messages }: { messages: ModelMessage[] } = await req.json();

  const toolkit = await getToolkit();

  const result = streamText({
    model: anthropic('claude-sonnet-4-6-20251001'),
    system: SYSTEM,
    messages,
    tools: toolkit.getTools(),
    stopWhen: stepCountIs(5),
  });

  return result.toTextStreamResponse();
}
