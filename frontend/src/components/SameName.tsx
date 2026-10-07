import { GitMerge, UserCheck, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { Empty, Loading } from "./ui";
import { api } from "../lib/api";
import { jdate, money, num } from "../lib/format";
import { useApi, useToast } from "../lib/hooks";
import { faDigits } from "../lib/jalali";

const validMobile = (m?: string) => {
  const d = (m ?? "").replace(/[۰-۹]/g, (x) => String("۰۱۲۳۴۵۶۷۸۹".indexOf(x))).replace(/\D/g, "");
  const n = d.startsWith("98") && d.length === 12 ? "0" + d.slice(2) : d.startsWith("9") && d.length === 10 ? "0" + d : d;
  return /^09\d{9}$/.test(n) ? n : "";
};

/** While a new customer is typed: "a customer with exactly this name exists - is it the same person?" */
export function SameNameHint({ name, mobile, exclude, onPick }: { name?: string; mobile?: string; exclude?: number; onPick: (c: any) => void }) {
  const toast = useToast();
  const [list, setList] = useState<any[]>([]);
  useEffect(() => {
    const n = (name ?? "").trim();
    if (n.split(/\s+/).length < 2 || n.length < 5) return setList([]);
    const t = setTimeout(() => {
      api(`/api/customers/same-name?name=${encodeURIComponent(n)}${exclude ? `&exclude=${exclude}` : ""}`).then(setList).catch(() => setList([]));
    }, 350);
    return () => clearTimeout(t);
  }, [name, exclude]);
  if (!list.length) return null;
  const m = validMobile(mobile);

  async function pick(c: any) {
    let chosen = c;
    if (m && m !== c.mobile && !(c.other_mobiles ?? []).includes(m)) {
      try {
        chosen = await api(`/api/customers/${c.id}/mobiles`, { body: { mobile: m } });
        toast(`شمارهٔ ${faDigits(m)} هم به پروندهٔ «${c.full_name}» اضافه شد`);
      } catch (e: any) {
        toast(e.message, "error");
        return;
      }
    }
    onPick(chosen);
  }

  return (
    <div className="space-y-2 rounded-2xl bg-amber-500/10 p-3 text-sm">
      <div className="flex items-center gap-1.5 font-bold text-amber-800 dark:text-amber-200"><Users size={16} />مشتری با همین نام و نام خانوادگی ثبت شده است. همان شخص است؟</div>
      {list.map((c) => (
        <div key={c.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl px-3 py-2" style={{ background: "var(--surface-solid)" }}>
          <div>
            <b>{c.full_name}</b> {c.code && <span className="num muted text-xs">کد {c.code}</span>}
            <div className="muted text-xs">
              <span className="num" dir="ltr">{c.mobile ?? c.mobile_raw ?? "بدون موبایل"}</span>
              {c.invoices > 0 && <> · {num(c.invoices)} فاکتور</>}{c.appointments > 0 && <> · {num(c.appointments)} نوبت</>}
              {c.last && <> · آخرین مراجعه {jdate(c.last)}</>}
            </div>
          </div>
          <button type="button" className="btn btn-sm" onClick={() => pick(c)}><UserCheck size={14} />بله، همین شخص است</button>
        </div>
      ))}
      <div className="muted text-xs">اگر شخص دیگری است، ادامه دهید؛ دو مشتری هم‌نام با شمارهٔ موبایل از هم جدا می‌شوند.</div>
    </div>
  );
}

/** Customers with the same full name: merge the ones that are one person, or mark them as different people. */
export function DuplicateCustomers({ onDone }: { onDone?: () => void }) {
  const toast = useToast();
  const { data, reload } = useApi<any[]>("/api/customers/duplicates");
  const [keep, setKeep] = useState<Record<number, number>>({});
  const [pick, setPick] = useState<Record<number, number[]>>({});
  const [busy, setBusy] = useState(false);
  if (!data) return <Loading />;
  if (!data.length) return <Empty text="مشتری هم‌نامی برای بررسی نیست ✓" />;

  async function run(fn: () => Promise<string>) {
    setBusy(true);
    try {
      toast(await fn());
      reload();
      onDone?.();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4 text-sm">
      <p className="muted text-xs leading-6">
        این مشتری‌ها نام و نام خانوادگی یکسان دارند. اگر یک نفرند، «اصلی» را انتخاب کنید و بقیه را تیک بزنید و «یکی کن» را بزنید:
        همهٔ فاکتورها، پرداخت‌ها، بدهی، بیعانه‌ها و نوبت‌ها به مشتری اصلی منتقل می‌شود و شماره و کد دیگرش هم در پرونده می‌ماند.
        اگر افراد متفاوتی هستند، «افراد متفاوت‌اند» را بزنید تا دیگر پیشنهاد نشوند.
      </p>
      <div className="muted text-xs">{faDigits(data.length)} گروه</div>
      {data.map((g, gi) => {
        const main = keep[gi] ?? g.customers[0].id;
        const others = pick[gi] ?? g.customers.filter((c: any) => c.id !== main).map((c: any) => c.id);
        const chosen = others.filter((id) => id !== main);
        return (
          <div key={g.customers.map((c: any) => c.id).join("-")} className="space-y-2 rounded-2xl border p-3" style={{ borderColor: "var(--border)" }}>
            <div className="font-bold">{g.name}</div>
            <div className="overflow-x-auto">
              <table className="table text-xs [&_td]:px-2 [&_th]:px-2">
                <thead><tr><th>اصلی</th><th>یکی شود</th><th>کد</th><th>موبایل</th><th>فاکتور / خرید</th><th>نوبت</th><th>بیعانهٔ باز</th><th>آخرین مراجعه</th><th>ثبت</th></tr></thead>
                <tbody>
                  {g.customers.map((c: any) => (
                    <tr key={c.id} className={c.id === main ? "bg-violet-500/5" : ""}>
                      <td><input type="radio" name={`keep-${gi}`} checked={c.id === main} onChange={() => setKeep({ ...keep, [gi]: c.id })} /></td>
                      <td>{c.id !== main && <input type="checkbox" checked={chosen.includes(c.id)}
                        onChange={(e) => setPick({ ...pick, [gi]: e.target.checked ? [...chosen, c.id] : chosen.filter((x) => x !== c.id) })} />}</td>
                      <td className="num">{c.code}</td>
                      <td className="num" dir="ltr">{c.mobile ?? c.mobile_raw ?? "—"}{c.other_mobiles?.length ? ` +${c.other_mobiles.length}` : ""}</td>
                      <td className="num">{num(c.invoices)} · {money(c.spent, false)}</td>
                      <td className="num">{num(c.appointments)}</td>
                      <td className="num">{c.deposits_held ? money(c.deposits_held, false) : "—"}</td>
                      <td>{c.last ? jdate(c.last) : "—"}</td>
                      <td>{jdate(c.created_at)}{c.source === "import" ? " · انتقالی" : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex flex-wrap gap-2">
              <button className="btn btn-sm btn-primary" disabled={busy || !chosen.length}
                onClick={() => {
                  const keepC = g.customers.find((c: any) => c.id === main);
                  if (!window.confirm(`${faDigits(chosen.length)} پرونده با «${keepC.full_name}» (کد ${keepC.code ?? "—"}) یکی شود؟\nهمهٔ سوابق، بدهی و بیعانه‌ها منتقل می‌شوند و این کار برگشت ندارد.`)) return;
                  run(async () => {
                    await api("/api/customers/merge", { body: { keep_id: main, drop_ids: chosen } });
                    return `${faDigits(chosen.length + 1)} پرونده یکی شد`;
                  });
                }}><GitMerge size={14} />یکی کن</button>
              <button className="btn btn-sm" disabled={busy}
                onClick={() => run(async () => {
                  await api("/api/customers/not-same", { body: { ids: g.customers.map((c: any) => c.id) } });
                  return "ثبت شد: این‌ها افراد متفاوتی هستند";
                })}>افراد متفاوت‌اند</button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
