/**
 * ⌘K command palette — generalised app infra. Jump to any console surface.
 * Brand-themed. Mounted in the Shell.
 *
 * `items` is supplied by the host (the shell builds it from capability nav),
 * so the palette has no hardcoded route knowledge.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

export interface CommandItem {
  to: string;
  label: string;
}

export function CommandBar({ items }: { items: CommandItem[] }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const nav = useNavigate();

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  if (!open) return null;
  const matches = items.filter((i) => i.label.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="fixed inset-0 z-50 bg-[color-mix(in_srgb,var(--ink)_40%,transparent)] backdrop-blur-sm grid place-items-start pt-[15vh] px-4" onClick={() => setOpen(false)}>
      <div className="mx-auto w-full max-w-lg rounded-lg border border-line-d bg-panel shadow-card overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Jump to…"
          className="w-full bg-transparent px-4 py-3 text-cloud outline-none border-b border-line-d placeholder:text-mute" />
        <div className="max-h-72 overflow-y-auto py-1">
          {matches.map((i) => (
            <button key={i.to} onClick={() => { nav(i.to); setOpen(false); setQ(""); }}
              className="block w-full text-left px-4 py-2 text-sm text-cloud hover:bg-panel-2">{i.label}</button>
          ))}
          {matches.length === 0 && <div className="px-4 py-3 text-mute text-sm">No matches.</div>}
        </div>
        <div className="border-t border-line-d px-4 py-2 font-mono text-[10.5px] text-mute">⌘K to toggle · esc to close</div>
      </div>
    </div>
  );
}
