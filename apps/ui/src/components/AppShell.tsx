import { Outlet, useLocation } from "react-router-dom";
import { ActivityToast } from "@/components/ActivityToast";
import { ApiOfflineBanner } from "@/components/ApiOfflineBanner";
import { DemoFallbackBanner } from "@/components/DemoFallbackBanner";
import { LeftNav } from "@/components/LeftNav";
import { SourcePanel } from "@/components/SourcePanel";
import { TopBar } from "@/components/TopBar";

export function AppShell() {
  const { pathname } = useLocation();
  const onChat = pathname === "/";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <TopBar />
      <DemoFallbackBanner />
      <ApiOfflineBanner />
      <div className="flex min-h-0 flex-1">
        <LeftNav />
        <main className="min-w-0 flex-1 overflow-hidden">
          <Outlet />
        </main>
        {onChat ? <SourcePanel /> : null}
      </div>
      <ActivityToast />
    </div>
  );
}
