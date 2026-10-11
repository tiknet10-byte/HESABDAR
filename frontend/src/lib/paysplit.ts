/** Splitting what the customer pays on an invoice between the lines it contains (all amounts in Rial, integers).
 *
 *  1. every invoice item has its gross amount and its group (service line, products, other);
 *  2. the invoice discount is shared over the items in proportion to their amounts (exactly as the server does);
 *  3. deposits are prepayments for SERVICES: they are never taken off product sales. A deposit taken for a service
 *     is taken off that service's line first; what is left of the deposits (or deposits without a line) is shared
 *     over the service groups in proportion to what each still has to pay;
 *  4. rounding goes by largest remainder, so the parts always add up exactly to the amount due.
 */
export type Group = { key: string; gross: number };

/** groups that deposits can never pay for */
export const NO_DEPOSIT = "products";

/** Share `amount` over `weights` (>= 0) proportionally; the parts are integers that add up to `amount` exactly.
 *  Integer (BigInt) math: Rial amounts multiplied together pass what a JavaScript number holds exactly. */
export function share(amount: number, weights: number[]): number[] {
  const w = weights.map((x) => BigInt(Math.max(0, Math.round(x))));
  const total = w.reduce((s, x) => s + x, 0n);
  if (amount <= 0 || total <= 0n) return weights.map(() => 0);
  const a = BigInt(Math.round(amount));
  const parts = w.map((x) => (x * a) / total);
  const rem = w.map((x) => (x * a) % total);
  let left = Number(a - parts.reduce((s, x) => s + x, 0n));
  const order = rem.map((r, i) => [r, i] as const).sort((p, q) => (p[0] === q[0] ? p[1] - q[1] : p[0] > q[0] ? -1 : 1));
  for (let k = 0; left > 0 && k < order.length; k++, left--) parts[order[k][1]] += 1n;
  return parts.map(Number);
}

const sum = (xs: number[]) => xs.reduce((s, x) => s + x, 0);

/** items: one entry per invoice item, in the invoice's order (keys repeat for items of the same line).
 *  Returns what is still due per group after the discount and the deposits. */
export function splitDue(items: Group[], discount: number, deposits: { amount: number; group?: string; credit?: boolean }[]): Record<string, number> {
  const gross = items.map((g) => Math.max(0, Math.round(g.gross)));
  const disc = share(Math.min(Math.max(0, discount), sum(gross)), gross);
  const net: Record<string, number> = {};
  items.forEach((g, i) => (net[g.key] = (net[g.key] ?? 0) + gross[i] - disc[i]));
  const keys = Object.keys(net);
  const room = keys.map((k) => (k === NO_DEPOSIT ? 0 : net[k])); // what service deposits may pay
  const service = deposits.filter((d) => !d.credit);
  let applied = Math.min(sum(service.map((d) => Math.max(0, d.amount))), sum(room));
  const dep = keys.map(() => 0);
  // deposits that belong to a line on this invoice go to that line first
  for (const d of service) {
    const i = keys.indexOf(d.group ?? "");
    if (i < 0 || applied <= 0) continue;
    const take = Math.min(Math.max(0, d.amount), room[i] - dep[i], applied);
    dep[i] += take;
    applied -= take;
  }
  // the rest of the deposits: over the services, proportionally to what each still has to pay
  if (applied > 0) share(applied, room.map((r, i) => r - dep[i])).forEach((x, i) => (dep[i] += x));
  // a customer credit (اعتبار) pays anything that is still due, products included (as the server does)
  const left = keys.map((k, i) => net[k] - dep[i]);
  const credit = Math.min(sum(deposits.filter((d) => d.credit).map((d) => Math.max(0, d.amount))), sum(left));
  if (credit > 0) share(credit, left).forEach((x, i) => (dep[i] += x));
  return Object.fromEntries(keys.map((k, i) => [k, net[k] - dep[i]]));
}
