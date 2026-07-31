import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { AppProvider } from "@/context/AppContext";
import { AdminPage } from "@/pages/AdminPage";
import { AmendPage } from "@/pages/AmendPage";
import { AuditPage } from "@/pages/AuditPage";
import { ChatPage } from "@/pages/ChatPage";
import { LibraryPage } from "@/pages/LibraryPage";
import { OntologyPage } from "@/pages/OntologyPage";
import { RunsPage } from "@/pages/RunsPage";
import { SpacesPage } from "@/pages/SpacesPage";
import { StudioPage } from "@/pages/StudioPage";
import { TrustPage } from "@/pages/TrustPage";

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
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AppProvider>
  );
}
