"use client";

import { Menu, X } from "lucide-react";
import { useState, type ReactNode } from "react";

import { AppSidebar } from "@/components/app-sidebar";
import { Logo, MockBadge } from "@/components/logo";
import { Button } from "@/components/ui/button";

export function AppShell({ children }: { children: ReactNode }) {
  const [drawerOpen, setDrawerOpen] = useState(false);

  return (
    <div className="flex h-dvh flex-col lg:grid lg:grid-cols-[17.5rem_1fr]">
      <aside aria-label="Sidebar" className="hidden min-h-0 border-r lg:block">
        <AppSidebar />
      </aside>

      <div className="flex items-center justify-between gap-2 border-b bg-panel px-4 py-2.5 lg:hidden">
        <Logo />
        <div className="flex items-center gap-2">
          <MockBadge />
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="Open workflows menu"
            aria-expanded={drawerOpen}
            onClick={() => setDrawerOpen(true)}
          >
            <Menu aria-hidden />
          </Button>
        </div>
      </div>

      {drawerOpen && (
        <div role="dialog" aria-modal="true" aria-label="Workflows menu" className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            aria-label="Close workflows menu"
            tabIndex={-1}
            className="absolute inset-0 bg-black/60"
            onClick={() => setDrawerOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 w-[min(20rem,85vw)] border-r">
            <AppSidebar onNavigate={() => setDrawerOpen(false)} />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label="Close workflows menu"
              className="absolute top-14 right-2"
              onClick={() => setDrawerOpen(false)}
            >
              <X aria-hidden />
            </Button>
          </div>
        </div>
      )}

      <main className="ecg-paper min-h-0 min-w-0 flex-1">{children}</main>
    </div>
  );
}
