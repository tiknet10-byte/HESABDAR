/** Splitting what the customer pays on an invoice between the lines it contains (all amounts in Rial, integers).
 *
 *  1. each group (service line, products, other) has its gross amount;
 *  2. the invoice discount is shared in proportion to the gross amounts;
 *  3. a deposit taken for a service is taken off that service's line first; what is left of the deposits
 *     (or deposits without a line) is shared in proportion to what each line still has to pay;
 *  4. rounding goes by largest remainder, so the parts always add up exactly to the amount due.
 */
export type Group = { key: string; gross: number };

/** Share `amount` over `weights` (>= 0) proportionally; the parts are integers that add up to `amount` exactly. */
export function share(amount: number, weights: number[]): number[] {
  const total = weights.reduce((s, w) => s + Math.max(0, w), 0);
  if (amount <= 0 || total <= 0) return weights.map(() => 0);
  const raw = weights.map((w) => (Math.max(0, w) * amount) / total);
  const parts = raw.map(Math.floor);
  let left = amount - parts.reduce((s, x) => s + x, 0);
  const order = raw.map((r, i) => [r - Math.floor(r), i] as const).sort((a, b) => b[0] - a[0] || a[1] - b[1]);
  for (let k = 0; left > 0 && k < order.length; k++, left--) parts[order[k][1]] += 1;
  return parts;
}

export function splitDue(groups: Group[], discount: number, deposits: { amount: number; group?: string }[]): Record<string, number> {
  const gross = groups.map((g) => Math.max(0, g.gross));
  const subtotal = gross.reduce((s, x) => s + x, 0);
  const disc = share(Math.min(Math.max(0, discount), subtotal), gross);
  const net = gross.map((g, i) => g - disc[i]);
  const total = net.reduce((s, x) => s + x, 0);
  let applied = Math.min(deposits.reduce((s, d) => s + Math.max(0, d.amount), 0), total);
  const dep = groups.map(() => 0);
  // deposits that belong to a line on this invoice go to that line first
  for (const d of deposits) {
    const i = groups.findIndex((g) => g.key === d.group);
    if (i < 0 || applied <= 0) continue;
    const take = Math.min(Math.max(0, d.amount), net[i] - dep[i], applied);
    dep[i] += take;
    applied -= take;
  }
  // the rest of the deposits: proportionally to what each line still has to pay
  if (applied > 0) {
    const rest = share(applied, net.map((n, i) => n - dep[i]));
    rest.forEach((x, i) => (dep[i] += x));
  }
  return Object.fromEntries(groups.map((g, i) => [g.key, net[i] - dep[i]]));
}
