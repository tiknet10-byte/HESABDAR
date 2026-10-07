import { AlertTriangle, Save, Star } from "lucide-react";
import { useEffect, useState } from "react";
import { Card, Loading } from "./ui";
import { api } from "../lib/api";
import { useApi, useToast } from "../lib/hooks";

type Entry = { pos: number[]; card: number[] };
const POS = ["pos"];
const CARD = ["card", "bank", "cash", "gateway"];

/** Settings: the POS terminals and cards of every line, of deposits and of product sales (first one = default). */
export default function AccountRouting({ refresh, canEdit }: { refresh: number; canEdit: boolean }) {
  const toast = useToast();
  const { data, reload } = useApi<any>("/api/accounts/routing", [refresh]);
  const accounts = (useApi<any[]>("/api/accounts", [refresh]).data ?? []).filter((a) => a.is_active);
  const [draft, setDraft] = useState<Record<string, Entry>>({});
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!data) return;
    const d: Record<string, Entry> = { deposits: data.deposits, products: data.products };
    data.lines.forEach((l: any) => (d[`line:${l.id}`] = { pos: l.pos, card: l.card }));
    setDraft(d);
  }, [data]);
  if (!data) return <Loading />;

  const rows: { key: string; label: string; color?: string; hint?: string }[] = [
    ...data.lines.map((l: any) => ({ key: `line:${l.id}`, label: `${l.code ? l.code + " · " : ""}${l.name}`, color: l.color })),
    { key: "deposits", label: "بیعانه‌ها", hint: "دریافت بیعانه از مشتری" },
    { key: "products", label: "فروش محصولات", hint: "فروش حضوری و آنلاین محصولات" },
  ];
  const pos = accounts.filter((a) => POS.includes(a.kind));
  const cards = accounts.filter((a) => CARD.includes(a.kind));
  const missing = rows.filter((r) => !draft[r.key]?.pos?.length || !draft[r.key]?.card?.length);

  const toggle = (key: string, kind: "pos" | "card", id: number) => {
    const e = draft[key] ?? { pos: [], card: [] };
    const list = e[kind].includes(id) ? e[kind].filter((x) => x !== id) : [...e[kind], id];
    setDraft({ ...draft, [key]: { ...e, [kind]: list } });
  };
  const makeDefault = (key: string, kind: "pos" | "card", id: number) => {
    const e = draft[key];
    setDraft({ ...draft, [key]: { ...e, [kind]: [id, ...e[kind].filter((x) => x !== id)] } });
  };

  async function save() {
    setBusy(true);
    try {
      const lines = Object.fromEntries(Object.entries(draft).filter(([k]) => k.startsWith("line:")).map(([k, v]) => [k.slice(5), v]));
      await api("/api/accounts/routing", { method: "PUT", body: { lines, deposits: draft.deposits, products: draft.products } });
      toast("حساب‌های پیش‌فرض ذخیره شد");
      reload();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  const chips = (key: string, kind: "pos" | "card", list: any[]) => {
    const chosen = draft[key]?.[kind] ?? [];
    if (!list.length) return <span className="muted text-xs">{kind === "pos" ? "کارتخوانی تعریف نشده" : "کارت یا حسابی تعریف نشده"}</span>;
    return (
      <div className="flex flex-wrap gap-1.5">
        {list.map((a) => {
          const on = chosen.includes(a.id);
          const first = chosen[0] === a.id;
          return (
            <span key={a.id} className={`inline-flex items-center overflow-hidden rounded-xl border text-xs font-semibold ${on ? "border-violet-500 bg-violet-500/10" : ""}`}
              style={on ? {} : { borderColor: "var(--border)" }}>
              <button type="button" disabled={!canEdit} className="px-2.5 py-1.5" onClick={() => toggle(key, kind, a.id)}>{on ? "✓ " : ""}{a.name}</button>
              {on && chosen.length > 1 && (
                <button type="button" disabled={!canEdit || first} onClick={() => makeDefault(key, kind, a.id)} title={first ? "پیش‌فرض" : "پیش‌فرض شود"}
                  className={`border-r px-1.5 py-1.5 ${first ? "text-amber-500" : "muted hover:text-amber-500"}`} style={{ borderColor: "var(--border)" }}>
                  <Star size={12} fill={first ? "currentColor" : "none"} />
                </button>
              )}
            </span>
          );
        })}
      </div>
    );
  };

  return (
    <Card title="کارتخوان و کارت هر لاین (انتخاب خودکار هنگام فاکتور)"
      actions={canEdit && <button className="btn btn-sm btn-primary" disabled={busy} onClick={save}><Save size={14} />ذخیره</button>}>
      <p className="muted mb-3 text-xs leading-6">
        برای هر لاین، برای بیعانه‌ها و برای فروش محصولات حداقل یک کارتخوان و یک کارت انتخاب کنید. هنگام ثبت فاکتور، مبلغ هر لاین خودکار روی
        کارتخوان همان لاین می‌نشیند (با یک کلیک می‌شود کارت را انتخاب کرد) و هنگام ثبت بیعانه، حساب بیعانه انتخاب می‌شود.
        اگر چند مورد انتخاب کنید، مورد ستاره‌دار پیش‌فرض است.
      </p>
      {missing.length > 0 && (
        <div className="mb-3 flex items-start gap-2 rounded-xl bg-amber-500/10 p-2.5 text-xs text-amber-800 dark:text-amber-200">
          <AlertTriangle size={15} className="shrink-0" />
          <span>هنوز کامل نیست: {missing.map((r) => r.label).join("، ")}{!cards.length || !pos.length ? " - اول در جدول بالا کارتخوان و کارت را اضافه کنید." : ""}</span>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="table text-sm [&_td]:px-2 [&_th]:px-2">
          <thead><tr><th>لاین / بخش</th><th>کارتخوان‌ها</th><th>کارت‌ها، حساب‌ها و صندوق</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className={missing.includes(r) ? "bg-amber-500/5" : ""}>
                <td className="whitespace-nowrap font-semibold">
                  <span className="flex items-center gap-2">{r.color && <span className="h-2.5 w-2.5 rounded-full" style={{ background: r.color }} />}{r.label}</span>
                  {r.hint && <span className="muted block text-[11px] font-normal">{r.hint}</span>}
                </td>
                <td>{chips(r.key, "pos", pos)}</td>
                <td>{chips(r.key, "card", cards)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
