import { Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Empty } from "./ui";
import { api } from "../lib/api";
import { jdate } from "../lib/format";
import { useToast } from "../lib/hooks";
import { faDigits } from "../lib/jalali";

export const MOBILE_ISSUE: Record<string, { label: string; cls: string }> = {
  invalid: { label: "موبایل اشتباه/ناقص", cls: "bg-rose-500/15 text-rose-600 dark:text-rose-300" },
  duplicate: { label: "موبایل تکراری", cls: "bg-amber-500/15 text-amber-700 dark:text-amber-300" },
  missing: { label: "بدون موبایل", cls: "bg-zinc-500/15 text-zinc-500" },
};

/** Customers with no service, deposit or invoice whose mobile is wrong/incomplete/duplicate (or missing) - delete in one go. */
export default function CustomerCleanup({ onDone }: { onDone?: () => void }) {
  const toast = useToast();
  const [issues, setIssues] = useState<string[]>(["invalid", "duplicate"]);
  const [items, setItems] = useState<any[] | null>(null);
  const [picked, setPicked] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);

  const load = () => {
    setItems(null);
    api(`/api/customers/cleanup?issues=${issues.join(",")}`).then((r) => { setItems(r.items); setPicked(r.items.map((c: any) => c.id)); })
      .catch((e) => { toast(e.message, "error"); setItems([]); });
  };
  useEffect(load, [issues.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  async function remove() {
    if (!picked.length || !window.confirm(`${faDigits(picked.length)} مشتری انتخاب‌شده حذف شوند؟ (این کار برگشت ندارد)`)) return;
    setBusy(true);
    try {
      const r = await api("/api/customers/bulk-delete", { body: { ids: picked } });
      toast(`${faDigits(r.deleted)} مشتری حذف شد${r.skipped ? ` · ${faDigits(r.skipped)} مورد سابقه داشت و حذف نشد` : ""}`);
      load();
      onDone?.();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  const all = !!items?.length && picked.length === items.length;
  return (
    <div className="space-y-3 text-sm">
      <div className="muted text-xs">فقط مشتریانی نمایش داده می‌شوند که <b>هیچ خدمت، نوبت، بیعانه یا فاکتوری ندارند</b>؛ مشتری دارای سابقه هرگز حذف نمی‌شود.</div>
      <div className="flex flex-wrap gap-2">
        {Object.entries(MOBILE_ISSUE).map(([k, v]) => {
          const on = issues.includes(k);
          return (
            <button key={k} type="button" onClick={() => setIssues(on ? issues.filter((x) => x !== k) : [...issues, k])}
              className={`rounded-xl px-3 py-1.5 text-xs font-semibold ${on ? "bg-violet-600 text-white" : "border"}`} style={on ? {} : { borderColor: "var(--border)" }}>
              {on ? "✓ " : ""}{v.label}
            </button>
          );
        })}
      </div>
      {items === null ? <div className="muted">…</div> : items.length === 0 ? <Empty text="مشتری مشکل‌داری پیدا نشد" /> : (
        <>
          <div className="max-h-96 overflow-y-auto rounded-2xl border" style={{ borderColor: "var(--border)" }}>
            <table className="table text-xs">
              <thead><tr>
                <th><input type="checkbox" checked={all} onChange={(e) => setPicked(e.target.checked ? items.map((c) => c.id) : [])} /></th>
                <th>کد</th><th>نام</th><th>شمارهٔ ثبت‌شده</th><th>مشکل</th><th>ثبت</th>
              </tr></thead>
              <tbody>
                {items.map((c) => {
                  const issue = MOBILE_ISSUE[c.mobile_issue ?? (c.mobile ? "" : "missing")];
                  return (
                    <tr key={c.id}>
                      <td><input type="checkbox" checked={picked.includes(c.id)} onChange={(e) => setPicked(e.target.checked ? [...picked, c.id] : picked.filter((x) => x !== c.id))} /></td>
                      <td className="num">{c.code}</td>
                      <td className="font-semibold">{c.full_name}</td>
                      <td className="num" dir="ltr">{c.mobile_raw ?? c.mobile ?? "—"}</td>
                      <td>{issue && <span className={`badge ${issue.cls}`}>{issue.label}</span>}</td>
                      <td className="muted">{jdate(c.created_at)}{c.source === "import" ? " · انتقالی" : ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <button className="btn w-full border-rose-300 text-rose-600 hover:bg-rose-500/10" disabled={busy || !picked.length} onClick={remove}>
            <Trash2 size={15} />حذف {faDigits(picked.length)} مشتری انتخاب‌شده
          </button>
        </>
      )}
    </div>
  );
}
