"use client";

import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";

import { Rail } from "@/components/Rail";

type DrawerCtx = { open: boolean; toggle: () => void; close: () => void };

const Drawer = createContext<DrawerCtx>({
  open: false,
  toggle: () => undefined,
  close: () => undefined,
});

/** Side drawer state, consumed by the Topbar's ☰ button. */
export function useDrawer(): DrawerCtx {
  return useContext(Drawer);
}

/** Application shell. On phones the sidebar is a sliding drawer; on desktop
 *  it is a fixed column and the menu button does not exist. */
export function Shell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const path = usePathname();

  // navigating closes the drawer
  useEffect(() => { setOpen(false); }, [path]);

  // with the drawer open, the content behind it does not scroll
  useEffect(() => {
    if (!open) return;
    const anterior = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = anterior; };
  }, [open]);

  return (
    <Drawer value={{ open, toggle: () => setOpen((v) => !v), close: () => setOpen(false) }}>
      <div className="shell">
        <Rail open={open} />
        <div className="rail-scrim" data-open={open} onClick={() => setOpen(false)}
             aria-hidden />
        <div className="main">{children}</div>
      </div>
    </Drawer>
  );
}
