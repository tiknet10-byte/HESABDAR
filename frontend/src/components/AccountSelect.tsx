import { useEffect } from "react";
import { useApi } from "../lib/hooks";

const KEY = "hesabdar.account.";

/** the account chosen last time for this kind of payment (on this computer), if it is still active */
const remembered = (key?: string): number => {
  if (!key) return 0;
  try { return Number(localStorage.getItem(KEY + key)) || 0; } catch { return 0; }
};

/** the cash box / cards / bank accounts (active ones); by default the one chosen last time (remember = a name for
 * the kind of payment, e.g. "payout"), else the cash box */
export default function AccountSelect({ value, onChange, remember }: { value: number; onChange: (id: number) => void; remember?: string }) {
  const accounts = (useApi<any[]>("/api/accounts").data ?? []).filter((a) => a.is_active);
  const cash = accounts.find((a) => a.kind === "cash");
  const last = remembered(remember);
  const v = value || (accounts.some((a) => a.id === last) ? last : 0) || cash?.id || accounts[0]?.id || 0;
  useEffect(() => { if (!value && v) onChange(v); }, [value, v]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <select className="input" value={v} onChange={(e) => {
      const id = Number(e.target.value);
      if (remember) try { localStorage.setItem(KEY + remember, String(id)); } catch { /* storage unavailable */ }
      onChange(id);
    }}>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
  );
}
