import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "@/app/Shell";
import { SystemProvider } from "@/hooks/useSystem";
import { EvaluationsPage } from "@/pages/EvaluationsPage";
import { IncidentDetailPage } from "@/pages/IncidentDetailPage";
import { IncidentsPage } from "@/pages/IncidentsPage";
import { InvestigationPage } from "@/pages/InvestigationPage";
import { InvestigationsPage } from "@/pages/InvestigationsPage";
import { McpPage } from "@/pages/McpPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { SystemPage } from "@/pages/SystemPage";

export function App() {
  return (
    <SystemProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<Navigate to="/incidents" replace />} />
            <Route path="incidents" element={<IncidentsPage />} />
            <Route path="incidents/:incidentId" element={<IncidentDetailPage />} />
            <Route path="investigations" element={<InvestigationsPage />} />
            <Route path="investigations/:investigationId" element={<InvestigationPage />} />
            <Route path="mcp" element={<McpPage />} />
            <Route path="evaluations" element={<EvaluationsPage />} />
            <Route path="system" element={<SystemPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </SystemProvider>
  );
}
