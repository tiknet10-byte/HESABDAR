import { useEffect } from "react";
import { useApi } from "../lib/hooks";

/** the cash box / cards / bank accounts (active ones), the cash box first chosen */
export default function AccountSelect({ value, onChange }: { value: number; onChange: (id: number) => void }) {
  const accounts = (useApi<any[]>("/api/accounts").data ?? []).filter((a) => a.is_active);
  const cash = accounts.find((a) => a.kind === "cash");
  const v = value || cash?.id || accounts[0]?.id || 0;
  useEffect(() => { if (!value && v) onChange(v); }, [value, v]); // eslint-disable-line react-hooks/exhaustive-deps
  return <select className="input" value={v} onChange={(e) => onChange(Number(e.target.value))}>{accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>;
}
