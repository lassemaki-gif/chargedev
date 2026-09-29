import { streamText, stepCountIs, tool, zodSchema } from 'ai';
import type { ModelMessage } from 'ai';
import { anthropic } from '@ai-sdk/anthropic';
import Stripe from 'stripe';
import { z } from 'zod';

export const maxDuration = 60;

function getStripe() {
  return new Stripe(process.env.STRIPE_SECRET_KEY!);
}

const SYSTEM = `You are a Stripe operations assistant for ChargedEV, a peer-to-peer EV charging marketplace.

Platform context:
- Buyers pay via Stripe Checkout one-time sessions in EUR
- ChargedEV takes a 20% platform fee; charger hosts receive 80% via Stripe Connect transfers
- Sellers use Stripe Express accounts for payouts
- All monetary values are in EUR (amounts are in cents, divide by 100)

Keep answers concise and data-focused. Format monetary amounts with € and two decimal places.`;

const tools = {
  retrieve_balance: tool({
    description: 'Retrieve the current Stripe account balance including available, pending, and Connect reserved funds.',
    inputSchema: zodSchema(z.object({})),
    execute: async () => getStripe().balance.retrieve(),
  }),

  list_charges: tool({
    description: 'List recent charges on the Stripe account.',
    inputSchema: zodSchema(z.object({
      limit: z.number().min(1).max(100).default(10).describe('Number of charges to return'),
    })),
    execute: async ({ limit }) => getStripe().charges.list({ limit, expand: ['data.transfer'] }),
  }),

  retrieve_charge: tool({
    description: 'Retrieve details of a specific charge by ID.',
    inputSchema: zodSchema(z.object({
      charge_id: z.string().describe('The charge ID (ch_...)'),
    })),
    execute: async ({ charge_id }) => getStripe().charges.retrieve(charge_id),
  }),

  list_transfers: tool({
    description: 'List recent Stripe Connect transfers to seller accounts.',
    inputSchema: zodSchema(z.object({
      limit: z.number().min(1).max(100).default(10).describe('Number of transfers to return'),
    })),
    execute: async ({ limit }) => getStripe().transfers.list({ limit }),
  }),

  list_payouts: tool({
    description: 'List recent payouts from the Stripe balance.',
    inputSchema: zodSchema(z.object({
      limit: z.number().min(1).max(100).default(10).describe('Number of payouts to return'),
    })),
    execute: async ({ limit }) => getStripe().payouts.list({ limit }),
  }),

  list_disputes: tool({
    description: 'List disputes on the Stripe account.',
    inputSchema: zodSchema(z.object({
      limit: z.number().min(1).max(100).default(10).describe('Number of disputes to return'),
    })),
    execute: async ({ limit }) => getStripe().disputes.list({ limit }),
  }),

  create_refund: tool({
    description: 'Create a refund for a charge. Always confirm with the user before executing.',
    inputSchema: zodSchema(z.object({
      charge_id: z.string().describe('The charge ID to refund (ch_...)'),
      amount: z.number().optional().describe('Amount in cents. Omit for full refund.'),
      reason: z.enum(['duplicate', 'fraudulent', 'requested_by_customer']).optional(),
    })),
    execute: async ({ charge_id, amount, reason }) =>
      getStripe().refunds.create({ charge: charge_id, amount, reason }),
  }),

  list_connected_accounts: tool({
    description: 'List Stripe Connect seller accounts on the platform.',
    inputSchema: zodSchema(z.object({
      limit: z.number().min(1).max(100).default(10).describe('Number of accounts to return'),
    })),
    execute: async ({ limit }) => getStripe().accounts.list({ limit }),
  }),

  retrieve_connected_account: tool({
    description: 'Retrieve details of a specific connected Stripe account.',
    inputSchema: zodSchema(z.object({
      account_id: z.string().describe('The connected account ID (acct_...)'),
    })),
    execute: async ({ account_id }) => getStripe().accounts.retrieve(account_id),
  }),
};

export async function POST(req: Request) {
  const { messages }: { messages: ModelMessage[] } = await req.json();

  const result = streamText({
    model: anthropic('claude-sonnet-4-6'),
    system: SYSTEM,
    messages,
    tools,
    stopWhen: stepCountIs(5),
  });

  return result.toTextStreamResponse();
}
