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

/** Estado da gaveta lateral, consumido pelo botão ☰ do Topbar. */
export function useDrawer(): DrawerCtx {
  return useContext(Drawer);
}

/** Casca da aplicação. No celular a barra lateral é uma gaveta deslizante;
 *  no desktop ela é uma coluna fixa e o botão de menu não existe. */
export function Shell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const path = usePathname();

  // navegar fecha a gaveta
  useEffect(() => { setOpen(false); }, [path]);

  // com a gaveta aberta, o conteúdo atrás não rola
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
