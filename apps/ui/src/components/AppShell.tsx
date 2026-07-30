import { Outlet } from "react-router-dom";
import { ApiOfflineBanner } from "@/components/ApiOfflineBanner";
import { LeftNav } from "@/components/LeftNav";
import { SourcePanel } from "@/components/SourcePanel";
import { TopBar } from "@/components/TopBar";

export function AppShell() {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <TopBar />
      <ApiOfflineBanner />
      <div className="flex min-h-0 flex-1">
        <LeftNav />
        <main className="min-w-0 flex-1 overflow-hidden">
          <Outlet />
        </main>
        <SourcePanel />
      </div>
    </div>
  );
}
