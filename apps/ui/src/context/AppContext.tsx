import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { fetchHealth } from "@/lib/api";
import { FIXTURE_ANSWER, FIXTURE_SPACES } from "@/lib/fixtures";
import type { AnswerEnvelope, AppRole, ContributingSource, SpaceSummary } from "@/lib/types";

type AppState = {
  apiOnline: boolean | null;
  spaces: SpaceSummary[];
  activeSpaceId: string | null;
  activeSpace: SpaceSummary | null;
  role: AppRole;
  setRole: (r: AppRole) => void;
  setActiveSpaceId: (id: string | null) => void;
  selectedValueId: string | null;
  selectValue: (id: string | null) => void;
  fixtureAnswer: AnswerEnvelope | null;
  showFixtureAnswer: () => void;
  clearAnswer: () => void;
  focusedSourceId: string | null;
  setFocusedSourceId: (id: string | null) => void;
  contributingSources: ContributingSource[];
  sourcePanelOpen: boolean;
  setSourcePanelOpen: (open: boolean) => void;
  navCollapsed: boolean;
  toggleNav: () => void;
};

const AppContext = createContext<AppState | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);
  const [activeSpaceId, setActiveSpaceId] = useState<string | null>(FIXTURE_SPACES[0].id);
  const [role, setRole] = useState<AppRole>("steward");
  const [selectedValueId, setSelectedValueId] = useState<string | null>(null);
  const [fixtureAnswer, setFixtureAnswer] = useState<AnswerEnvelope | null>(null);
  const [focusedSourceId, setFocusedSourceId] = useState<string | null>(null);
  const [sourcePanelOpen, setSourcePanelOpen] = useState(true);
  const [navCollapsed, setNavCollapsed] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    const tick = () => {
      void fetchHealth(ctrl.signal).then((body) => {
        setApiOnline(body?.status === "ok");
      });
    };
    tick();
    const id = window.setInterval(tick, 8000);
    return () => {
      ctrl.abort();
      window.clearInterval(id);
    };
  }, []);

  const activeSpace = useMemo(
    () => FIXTURE_SPACES.find((s) => s.id === activeSpaceId) ?? null,
    [activeSpaceId],
  );

  const selectValue = useCallback((id: string | null) => {
    setSelectedValueId(id);
    if (id) {
      setSourcePanelOpen(true);
      setFocusedSourceId(FIXTURE_ANSWER.contributing_sources[0]?.ref_id ?? null);
    }
  }, []);

  const showFixtureAnswer = useCallback(() => {
    setFixtureAnswer(FIXTURE_ANSWER);
    setSelectedValueId(null);
    setFocusedSourceId(null);
    setSourcePanelOpen(true);
  }, []);

  const clearAnswer = useCallback(() => {
    setFixtureAnswer(null);
    setSelectedValueId(null);
    setFocusedSourceId(null);
  }, []);

  const value: AppState = {
    apiOnline,
    spaces: FIXTURE_SPACES,
    activeSpaceId,
    activeSpace,
    role,
    setRole,
    setActiveSpaceId,
    selectedValueId,
    selectValue,
    fixtureAnswer,
    showFixtureAnswer,
    clearAnswer,
    focusedSourceId,
    setFocusedSourceId,
    contributingSources: fixtureAnswer?.contributing_sources ?? [],
    sourcePanelOpen,
    setSourcePanelOpen,
    navCollapsed,
    toggleNav: () => setNavCollapsed((c) => !c),
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp outside AppProvider");
  return ctx;
}
