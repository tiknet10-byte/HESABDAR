import { Printer } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import { money } from "../lib/format";
import { faDigits, formatJ, parseLocal, toLocalIso } from "../lib/jalali";

const STATUS: Record<string, string> = { booked: "رزرو", done: "انجام شد", cancelled: "لغو", no_show: "نیامد" };
const hm = (iso: string) => faDigits(iso.slice(11, 16));
const end = (iso: string, m: number) => hm(toLocalIso(new Date(parseLocal(iso).getTime() + m * 60000)));

/** Clean A4 sheet of appointments: one page (or more) per line, grouped by day. Opens the print dialog by itself. */
export default function PrintAppointments() {
  const [params] = useSearchParams();
  const start = params.get("start") ?? toLocalIso(new Date()).slice(0, 10);
  const stop = params.get("end") ?? start;
  const ids = (params.get("lines") ?? "").split(",").map(Number).filter(Boolean);
  const withCancelled = params.get("all") === "1";
  const [data, setData] = useState<{ line: any; list: any[] }[] | null>(null);
  const [salon, setSalon] = useState("");

  useEffect(() => {
    document.title = "چاپ نوبت‌ها";
    api<Record<string, any>>("/api/settings").then((s) => setSalon(s["salon.name"] || "")).catch(() => {});
    api<any[]>("/api/lines").then(async (lines) => {
      const chosen = lines.filter((l) => !ids.length || ids.includes(l.id));
      const out = await Promise.all(chosen.map(async (line) => ({
        line,
        list: (await api<any[]>(`/api/appointments?line_id=${line.id}&start=${start}&end=${stop}`))
          .filter((a) => withCancelled || a.status !== "cancelled"),
      })));
      setData(out);
    }).catch(() => setData([]));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (data) setTimeout(() => window.print(), 400);
  }, [data]);

  const range = start === stop ? formatJ(start + "T12:00", false) : `${formatJ(start + "T12:00", false)} تا ${formatJ(stop + "T12:00", false)}`;
  return (
    <div dir="rtl" className="print-sheet min-h-screen bg-white text-black">
      <div className="no-print sticky top-0 flex items-center justify-between gap-2 border-b bg-white px-6 py-3">
        <div className="text-sm">پیش‌نمایش چاپ · کاغذ A4 · هر لاین در صفحهٔ جداگانه</div>
        <div className="flex gap-2">
          <button className="btn btn-primary btn-sm" onClick={() => window.print()}><Printer size={14} />چاپ</button>
          <button className="btn btn-sm" onClick={() => window.close()}>بستن</button>
        </div>
      </div>
      {!data ? <div className="p-8">در حال آماده‌سازی…</div> : data.length === 0 ? <div className="p-8">لاینی انتخاب نشده است.</div> : data.map(({ line, list }) => {
        const days = list.reduce((g: Record<string, any[]>, a) => ((g[a.start_at.slice(0, 10)] ||= []).push(a), g), {});
        return (
          <section key={line.id} className="a4-page mx-auto my-6 max-w-[210mm] bg-white px-8 py-6 shadow print:my-0 print:shadow-none">
            <header className="mb-4 flex items-end justify-between border-b-2 border-black pb-2">
              <div>
                <div className="text-xl font-extrabold">نوبت‌های لاین «{line.name}»</div>
                <div className="text-sm">{range}</div>
              </div>
              <div className="text-left text-xs">
                {salon && <div className="font-bold">{salon}</div>}
                <div>تعداد نوبت: {faDigits(list.filter((a) => a.status !== "cancelled").length)}</div>
                <div>تاریخ چاپ: {formatJ(new Date())}</div>
              </div>
            </header>
            {list.length === 0 ? <div className="py-6 text-center text-sm">در این بازه نوبتی ثبت نشده است.</div> : Object.entries(days).map(([d, rows]) => (
              <div key={d} className="day-block mb-4">
                <div className="mb-1 rounded bg-zinc-100 px-2 py-1 text-sm font-bold">{formatJ(d + "T12:00", false)} · {faDigits(rows.length)} نوبت</div>
                <table className="print-table w-full border-collapse text-[12px]">
                  <thead>
                    <tr>
                      <th className="w-8">#</th><th className="w-20">ساعت</th><th>مشتری</th><th className="w-28">موبایل</th><th>خدمت</th>
                      <th className="w-24">پرسنل</th><th className="w-24">بیعانه</th><th className="w-16">وضعیت</th><th className="w-28">توضیحات</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((a, i) => (
                      <tr key={a.id} className={a.status === "cancelled" ? "line-through opacity-60" : ""}>
                        <td className="text-center">{faDigits(i + 1)}</td>
                        <td className="text-center font-bold">{hm(a.start_at)}–{end(a.start_at, a.duration_minutes ?? 60)}</td>
                        <td className="font-semibold">{a.customer}</td>
                        <td className="text-center" dir="ltr">{a.customer_mobile ?? ""}</td>
                        <td>{a.service ?? "—"}</td>
                        <td>{a.staff ?? ""}</td>
                        <td>{a.deposits?.length ? a.deposits.map((x: any) => money(x.amount)).join(" + ") : ""}</td>
                        <td className="text-center">{STATUS[a.status] ?? a.status}</td>
                        <td>{a.notes}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </section>
        );
      })}
    </div>
  );
}
