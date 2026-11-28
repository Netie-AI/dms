import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { AppProvider } from "@/context/AppContext";
import { useFeatures } from "@/lib/features";
import { AdminPage } from "@/pages/AdminPage";
import { AmendPage } from "@/pages/AmendPage";
import { AuditPage } from "@/pages/AuditPage";
import { ChatPage } from "@/pages/ChatPage";
import { InsightsPage } from "@/pages/InsightsPage";
import { LibraryPage } from "@/pages/LibraryPage";
import { OntologyPage } from "@/pages/OntologyPage";
import { RunsPage } from "@/pages/RunsPage";
import { SpacesPage } from "@/pages/SpacesPage";
import { StudioPage } from "@/pages/StudioPage";
import { TrustPage } from "@/pages/TrustPage";

function FeatureRoute({ feature, children }: { feature: string; children: ReactNode }) {
  const features = useFeatures();
  if (features === null) return null;
  if (features[feature] !== true) return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  return (
    <AppProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<ChatPage />} />
            <Route path="spaces" element={<SpacesPage />} />
            <Route path="library" element={<LibraryPage />} />
            <Route path="studio" element={<StudioPage />} />
            <Route path="ontology" element={<OntologyPage />} />
            <Route path="amend" element={<AmendPage />} />
            <Route path="audit" element={<AuditPage />} />
            <Route path="trust" element={<TrustPage />} />
            <Route path="runs" element={<RunsPage />} />
            <Route path="admin" element={<AdminPage />} />
            <Route
              path="insights"
              element={
                <FeatureRoute feature="insight_proof">
                  <InsightsPage />
                </FeatureRoute>
              }
            />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AppProvider>
  );
}
