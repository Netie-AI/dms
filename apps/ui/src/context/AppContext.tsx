import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { fetchHealth, fetchSpaces, postAsk } from "@/lib/api";
import { FIXTURE_SPACES, SUGGESTED_QUESTIONS } from "@/lib/fixtures";
import type { AnswerEnvelope, AppRole, ContributingSource, SpaceSummary } from "@/lib/types";

export type ChatMessage =
  | { id: string; role: "user"; text: string }
  | { id: string; role: "assistant"; envelope: AnswerEnvelope };

function newSessionId(): string {
  const rand =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID().replace(/-/g, "").slice(0, 16)
      : Math.random().toString(16).slice(2, 18);
  return `ses_${rand}`;
}

export type ActivityState = {
  label: string;
  progress?: number | null;
} | null;

type AppState = {
  apiOnline: boolean | null;
  askMode: "demo" | "live" | null;
  demoFallbackEnabled: boolean | null;
  databaseConfigured: boolean | null;
  cortexContractOk: boolean | null;
  cortexTrustOk: boolean | null;
  cortexTrustHint: string | null;
  spaces: SpaceSummary[];
  spacesFromApi: boolean;
  activeSpaceId: string | null;
  activeSpace: SpaceSummary | null;
  role: AppRole;
  setRole: (r: AppRole) => void;
  setActiveSpaceId: (id: string | null) => void;
  selectedValueId: string | null;
  selectValue: (id: string | null) => void;
  sessionId: string;
  messages: ChatMessage[];
  latestAnswer: AnswerEnvelope | null;
  suggestions: string[];
  askError: string | null;
  asking: boolean;
  askQueueDepth: number;
  composerPaused: boolean;
  composerPauseReason: string | null;
  activity: ActivityState;
  setActivity: (a: ActivityState) => void;
  /** Tables the next question is grounded in. Empty means the whole Space. */
  groundedTables: string[];
  groundedLabels: string[];
  setGrounded: (tables: string[], labels?: string[]) => void;
  ask: (question: string) => Promise<void>;
  clearThread: () => void;
  focusedSourceId: string | null;
  setFocusedSourceId: (id: string | null) => void;
  contributingSources: ContributingSource[];
  sourcePanelOpen: boolean;
  setSourcePanelOpen: (open: boolean) => void;
  navCollapsed: boolean;
  toggleNav: () => void;
};

const AppContext = createContext<AppState | null>(null);

function classifyPause(message: string): string | null {
  const m = message.toLowerCase();
  if (m.includes("429") || m.includes("pool_saturated") || m.includes("rate")) {
    return "Paused — pool busy or rate-limited. Retry shortly.";
  }
  if (m.includes("manifest_unknown_issuer") || m.includes("unknown_issuer")) {
    return "Paused — OpenVault JWKS trust. Restart OV with pinned OPENVAULT_HOME, then Cortex JWKS refresh.";
  }
  if (m.includes("submit") && m.includes("404")) {
    return "Paused — Cortex contract routes missing. Restart Cortex on :8010.";
  }
  return null;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [apiOnline, setApiOnline] = useState<boolean | null>(null);
  const [askMode, setAskMode] = useState<"demo" | "live" | null>(null);
  const [demoFallbackEnabled, setDemoFallbackEnabled] = useState<boolean | null>(null);
  const [databaseConfigured, setDatabaseConfigured] = useState<boolean | null>(null);
  const [cortexContractOk, setCortexContractOk] = useState<boolean | null>(null);
  const [cortexTrustOk, setCortexTrustOk] = useState<boolean | null>(null);
  const [cortexTrustHint, setCortexTrustHint] = useState<string | null>(null);
  const [spaces, setSpaces] = useState<SpaceSummary[]>(FIXTURE_SPACES);
  const [spacesFromApi, setSpacesFromApi] = useState(false);
  const [activeSpaceId, setActiveSpaceId] = useState<string | null>(FIXTURE_SPACES[0].id);
  const [role, setRole] = useState<AppRole>("steward");
  const [selectedValueId, setSelectedValueId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState(newSessionId);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [askError, setAskError] = useState<string | null>(null);
  const [groundedTables, setGroundedTables] = useState<string[]>([]);
  const [groundedLabels, setGroundedLabels] = useState<string[]>([]);
  const [asking, setAsking] = useState(false);
  const [askQueueDepth, setAskQueueDepth] = useState(0);
  const [composerPaused, setComposerPaused] = useState(false);
  const [composerPauseReason, setComposerPauseReason] = useState<string | null>(null);
  const [activity, setActivity] = useState<ActivityState>(null);
  const [focusedSourceId, setFocusedSourceId] = useState<string | null>(null);
  const [sourcePanelOpen, setSourcePanelOpen] = useState(true);
  const [navCollapsed, setNavCollapsed] = useState(false);
  const queueRef = useRef<string[]>([]);
  const drainingRef = useRef(false);
  const setGrounded = useCallback((tables: string[], labels: string[] = []) => {
    setGroundedTables(tables);
    setGroundedLabels(labels.length ? labels : tables);
  }, []);

  const groundedTablesRef = useRef(groundedTables);
  const activeSpaceIdRef = useRef(activeSpaceId);
  const sessionIdRef = useRef(sessionId);
  activeSpaceIdRef.current = activeSpaceId;
  groundedTablesRef.current = groundedTables;
  sessionIdRef.current = sessionId;

  useEffect(() => {
    const ctrl = new AbortController();
    const tick = () => {
      void fetchHealth(ctrl.signal).then((body) => {
        setApiOnline(body?.status === "ok");
        if (body?.ask_mode) setAskMode(body.ask_mode);
        if (typeof body?.demo_fallback === "boolean") {
          setDemoFallbackEnabled(body.demo_fallback);
        }
        if (typeof body?.database_configured === "boolean") {
          setDatabaseConfigured(body.database_configured);
        }
        const c = body?.dependencies?.cortex;
        const ov = body?.dependencies?.openvault;
        if (c) {
          setCortexContractOk(c.contract_routes !== false && c.ok !== false);
          const refreshOk = c.jwks_refresh?.ok !== false;
          const trustHint =
            c.error ||
            c.jwks_refresh?.hint ||
            ov?.trust?.hint ||
            null;
          setCortexTrustOk(refreshOk && (ov?.trust?.jwks_ok !== false || ov?.ok !== false));
          setCortexTrustHint(trustHint);
        }
      });
    };
    tick();
    const id = window.setInterval(tick, 8000);
    return () => {
      ctrl.abort();
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    void fetchSpaces(ctrl.signal)
      .then((list) => {
        if (!list.length) return;
        setSpaces(list);
        setSpacesFromApi(true);
        setActiveSpaceId((prev) => {
          if (prev && list.some((s) => s.id === prev)) return prev;
          return list[0]?.id ?? null;
        });
      })
      .catch(() => {
        setSpacesFromApi(false);
      });
    return () => ctrl.abort();
  }, []);

  const activeSpace = useMemo(
    () => spaces.find((s) => s.id === activeSpaceId) ?? null,
    [spaces, activeSpaceId],
  );

  const latestAnswer = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (m.role === "assistant") return m.envelope;
    }
    return null;
  }, [messages]);

  const suggestions = useMemo(
    () => (latestAnswer?.suggestions?.length ? latestAnswer.suggestions : SUGGESTED_QUESTIONS),
    [latestAnswer],
  );

  const selectValue = useCallback(
    (id: string | null) => {
      setSelectedValueId(id);
      if (id && latestAnswer?.contributing_sources[0]) {
        setSourcePanelOpen(true);
        setFocusedSourceId(latestAnswer.contributing_sources[0].ref_id);
      }
    },
    [latestAnswer],
  );

  const runAsk = useCallback(async (question: string) => {
    const trimmed = question.trim();
    if (!trimmed) return;
    const userId = `u_${Date.now()}`;
    setMessages((prev) => [...prev, { id: userId, role: "user", text: trimmed }]);
    setAsking(true);
    setActivity({ label: "Asking Cortex…", progress: null });
    setAskError(null);
    setComposerPaused(false);
    setComposerPauseReason(null);
    setSelectedValueId(null);
    setFocusedSourceId(null);
    try {
      const envelope = await postAsk({
        question: trimmed,
        space_id: activeSpaceIdRef.current,
        session_id: sessionIdRef.current,
        grounded_tables: groundedTablesRef.current,
      });
      setMessages((prev) => [
        ...prev,
        { id: envelope.answer_id || `a_${Date.now()}`, role: "assistant", envelope },
      ]);
      setSourcePanelOpen(true);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "ask failed";
      setAskError(msg);
      const pause = classifyPause(msg);
      if (pause) {
        setComposerPaused(true);
        setComposerPauseReason(pause);
      }
    } finally {
      setAsking(false);
      setActivity(null);
    }
  }, []);

  const drainQueue = useCallback(async () => {
    if (drainingRef.current) return;
    drainingRef.current = true;
    try {
      while (queueRef.current.length) {
        const next = queueRef.current.shift()!;
        setAskQueueDepth(queueRef.current.length);
        await runAsk(next);
      }
    } finally {
      drainingRef.current = false;
      setAskQueueDepth(queueRef.current.length);
    }
  }, [runAsk]);

  const ask = useCallback(
    async (question: string) => {
      const trimmed = question.trim();
      if (!trimmed) return;
      if (asking || drainingRef.current) {
        queueRef.current.push(trimmed);
        setAskQueueDepth(queueRef.current.length);
        setActivity({
          label: `Queued (${queueRef.current.length}) — sending after current answer`,
          progress: null,
        });
        return;
      }
      await runAsk(trimmed);
      await drainQueue();
    },
    [asking, drainQueue, runAsk],
  );

  const clearThread = useCallback(() => {
    queueRef.current = [];
    setAskQueueDepth(0);
    setMessages([]);
    setAskError(null);
    setComposerPaused(false);
    setComposerPauseReason(null);
    setSelectedValueId(null);
    setFocusedSourceId(null);
    setSessionId(newSessionId());
  }, []);

  const value: AppState = {
    apiOnline,
    askMode,
    demoFallbackEnabled,
    databaseConfigured,
    cortexContractOk,
    cortexTrustOk,
    cortexTrustHint,
    spaces,
    spacesFromApi,
    activeSpaceId,
    groundedTables,
    groundedLabels,
    setGrounded,
    activeSpace,
    role,
    setRole,
    setActiveSpaceId,
    selectedValueId,
    selectValue,
    sessionId,
    messages,
    latestAnswer,
    suggestions,
    askError,
    asking,
    askQueueDepth,
    composerPaused,
    composerPauseReason,
    activity,
    setActivity,
    ask,
    clearThread,
    focusedSourceId,
    setFocusedSourceId,
    contributingSources: latestAnswer?.contributing_sources ?? [],
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
