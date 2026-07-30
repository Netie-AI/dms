import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { AppProvider } from "@/context/AppContext";
import { ChatPage } from "@/pages/ChatPage";
import {
  AdminPage,
  AmendPage,
  AuditPage,
  LibraryPage,
  RunsPage,
  StudioPage,
} from "@/pages/StubPages";

export default function App() {
  return (
    <AppProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<ChatPage />} />
            <Route path="library" element={<LibraryPage />} />
            <Route path="studio" element={<StudioPage />} />
            <Route path="amend" element={<AmendPage />} />
            <Route path="audit" element={<AuditPage />} />
            <Route path="runs" element={<RunsPage />} />
            <Route path="admin" element={<AdminPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AppProvider>
  );
}
