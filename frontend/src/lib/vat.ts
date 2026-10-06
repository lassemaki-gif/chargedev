export const VAT_RATE = 0.255
export const VAT_LABEL = "ALV 25,5 %"

/** VAT amount extracted from a gross (VAT-inclusive) price. */
export function vatFromGross(gross: number): number {
  return Math.round((gross * VAT_RATE / (1 + VAT_RATE)) * 100) / 100
}

/** Net (ex-VAT) amount from a gross (VAT-inclusive) price. */
export function netFromGross(gross: number): number {
  return Math.round((gross / (1 + VAT_RATE)) * 100) / 100
}
