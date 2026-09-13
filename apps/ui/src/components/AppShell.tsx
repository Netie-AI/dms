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
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-x-hidden">
      <TopBar />
      <DemoFallbackBanner />
      <ApiOfflineBanner />
      <div className="flex min-h-0 min-w-0 flex-1">
        <div
          data-testid="left-nav-slot"
          className="max-lg:w-0 max-lg:min-w-0 max-lg:shrink-0 max-lg:overflow-visible lg:contents"
        >
          <LeftNav />
        </div>
        <main className="min-w-0 flex-1 overflow-hidden">
          <Outlet />
        </main>
        {onChat ? (
          <div
            data-testid="source-panel-slot"
            className="max-lg:w-0 max-lg:min-w-0 max-lg:shrink-0 max-lg:overflow-visible lg:contents"
          >
            <SourcePanel />
          </div>
        ) : null}
      </div>
      <ActivityToast />
    </div>
  );
}
