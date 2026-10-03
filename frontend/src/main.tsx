import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import "./index.css";
import { api, getToken, setToken } from "./lib/api";
import { AuthContext, type Toast, ToastContext, type User } from "./lib/hooks";
import Appointments from "./pages/Appointments";
import Assistant from "./pages/Assistant";
import Customers from "./pages/Customers";
import Dashboard from "./pages/Dashboard";
import Deposits from "./pages/Deposits";
import Expenses from "./pages/Expenses";
import Invoices from "./pages/Invoices";
import Ledger from "./pages/Ledger";
import Login from "./pages/Login";
import Reconciliation from "./pages/Reconciliation";
import Reports from "./pages/Reports";
import Scan from "./pages/Scan";
import SettingsPage from "./pages/Settings";
import { Loading } from "./components/ui";

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
  }, []);

  useEffect(() => {
    if (!getToken()) return setReady(true);
    api<User>("/api/auth/me").then(setUser).catch(() => setToken(null)).finally(() => setReady(true));
  }, []);
  useEffect(() => {
    const h = () => setUser(null);
    window.addEventListener("hesabdar:logout", h);
    return () => window.removeEventListener("hesabdar:logout", h);
  }, []);

  const toast = useCallback((text: string, kind: Toast["kind"] = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);

  if (!ready) return <Loading />;
  return (
    <AuthContext.Provider value={{ user, setUser, logout }}>
      <ToastContext.Provider value={toast}>
        {!user ? (
          <Login />
        ) : (
          <BrowserRouter>
            <Routes>
              <Route element={<Layout />}>
                <Route index element={<Dashboard />} />
                <Route path="invoices" element={<Invoices />} />
                <Route path="deposits" element={<Deposits />} />
                <Route path="appointments" element={<Appointments />} />
                <Route path="customers" element={<Customers />} />
                <Route path="reconciliation" element={<Reconciliation />} />
                <Route path="scan" element={<Scan />} />
                <Route path="assistant" element={<Assistant />} />
                <Route path="reports" element={<Reports />} />
                <Route path="expenses" element={<Expenses />} />
                <Route path="ledger" element={<Ledger />} />
                <Route path="settings" element={<SettingsPage />} />
                <Route path="*" element={<Dashboard />} />
              </Route>
            </Routes>
          </BrowserRouter>
        )}
        <div className="pointer-events-none fixed bottom-4 left-4 z-[60] flex flex-col gap-2">
          {toasts.map((t) => (
            <div key={t.id} className={`fade-up pointer-events-auto rounded-2xl px-4 py-3 text-sm font-semibold text-white shadow-xl ${t.kind === "error" ? "bg-rose-500" : t.kind === "info" ? "bg-sky-500" : "bg-emerald-500"}`}>
              {t.text}
            </div>
          ))}
        </div>
      </ToastContext.Provider>
    </AuthContext.Provider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
