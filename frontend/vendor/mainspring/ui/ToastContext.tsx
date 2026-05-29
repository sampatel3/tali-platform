/**
 * Toasts — generalised app infra. Brand-themed.
 * useToast().push({ kind, message }); <ToastProvider/> renders them.
 */
import { createContext, useCallback, useContext, useState, ReactNode } from "react";

type ToastKind = "info" | "success" | "warn" | "error";
interface Toast { id: number; kind: ToastKind; message: string }
interface ToastApi { push: (t: { kind?: ToastKind; message: string }) => void }

const Ctx = createContext<ToastApi>({ push: () => {} });
let _id = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((t: { kind?: ToastKind; message: string }) => {
    const id = ++_id;
    setToasts((prev) => [...prev, { id, kind: t.kind ?? "info", message: t.message }]);
    setTimeout(() => setToasts((prev) => prev.filter((x) => x.id !== id)), 4200);
  }, []);
  return (
    <Ctx.Provider value={{ push }}>
      {children}
      <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2 max-w-sm">
        {toasts.map((t) => {
          const tone = t.kind === "success" ? "border-accent text-accent"
            : t.kind === "warn" ? "border-warn text-warn"
            : t.kind === "error" ? "border-danger text-danger"
            : "border-line-d text-cloud";
          return (
            <div key={t.id} role="status"
              className={`rounded-lg border bg-panel ${tone} px-4 py-3 text-sm shadow-card animate-[fade_.15s_ease]`}>
              <span className="text-cloud">{t.message}</span>
            </div>
          );
        })}
      </div>
    </Ctx.Provider>
  );
}

export function useToast(): ToastApi {
  return useContext(Ctx);
}
