import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { fetchHealth, fetchLibrarySources, fetchSpaces, postAsk } from "@/lib/api";
import { sourcesForPanel } from "@/lib/sourcePanel";
import { FIXTURE_SPACES, SUGGESTED_QUESTIONS } from "@/lib/fixtures";
import { storedProductMode, type ProductMode } from "@/lib/productMode";
import {
  LG_MIN_WIDTH_PX,
  navStartsCollapsed,
  shouldExpandSourcesDock,
  isLgNow,
  useIsLg,
} from "@/lib/viewport";
import type { AnswerEnvelope, AppRole, ContributingSource, SpaceSummary } from "@/lib/types";

const DESKTOP_MEDIA = () => ({ matches: true });

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
  spacesPersisted: boolean | null;
  spacesStorageHint: string | null;
  cortexContractOk: boolean | null;
  /** contract_routes only — distinct from cortex.ok (JWKS refresh can fail while routes exist). */
  cortexContractRoutesOk: boolean | null;
  cortexTrustOk: boolean | null;
  cortexTrustHint: string | null;
  spaces: SpaceSummary[];
  spacesFromApi: boolean;
  activeSpaceId: string | null;
  activeSpace: SpaceSummary | null;
  /** Live count from /v1/library/sources or /v1/spaces/{id}/sources (SPACE-UI). */
  scopedSourceCount: number | null;
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
  /** The lg dock and the phone sheet. Read the one that applies through useSourcePanelOpen(). */
  dockOpen: boolean;
  sheetOpen: boolean;
  /** Opens or closes the dock at lg and the sheet below it, by the width right now. */
  setSourcePanelOpen: (open: boolean) => void;
  /** The lg rail/dock and the phone drawer. Read the one that applies through useNavCollapsed(). */
  navLgCollapsed: boolean;
  navDrawerOpen: boolean;
  toggleNav: () => void;
  closeNavDrawer: () => void;
  productMode: ProductMode;
  setProductMode: (mode: ProductMode) => void;
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
  const [spacesPersisted, setSpacesPersisted] = useState<boolean | null>(null);
  const [spacesStorageHint, setSpacesStorageHint] = useState<string | null>(null);
  const [cortexContractOk, setCortexContractOk] = useState<boolean | null>(null);
  const [cortexContractRoutesOk, setCortexContractRoutesOk] = useState<boolean | null>(null);
  const [cortexTrustOk, setCortexTrustOk] = useState<boolean | null>(null);
  const [cortexTrustHint, setCortexTrustHint] = useState<string | null>(null);
  const [spaces, setSpaces] = useState<SpaceSummary[]>(FIXTURE_SPACES);
  const [spacesFromApi, setSpacesFromApi] = useState(false);
  // Keyed by Space, so a count read for one Space is never shown under another.
  const [scoped, setScoped] = useState<{ id: string | null; n: number | null }>({ id: null, n: null });
  const [activeSpaceId, setActiveSpaceIdState] = useState<string | null>(FIXTURE_SPACES[0].id);
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
  // Sources is two independent pieces of state, never one value reseeded on a width change:
  // the lg dock (open until the user collapses it) and the phone sheet (closed until the user opens
  // it). What is "open" is derived by each reader from the width it renders at (useSourcePanelOpen),
  // so no commit can pair the phone layout with the desktop dock state. Crossing lg therefore never
  // opens the sheet, and coming back restores the dock exactly as the user left it.
  const [dockOpen, setDockOpen] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const isLg = useIsLg();
  // The sheet is closed whenever the page is at lg, so a sheet left open and then widened past lg
  // does not come back open on the next narrowing.
  useLayoutEffect(() => {
    if (isLg) setSheetOpen(false);
  }, [isLg]);
  const setSourcePanelOpen = useCallback((open: boolean) => {
    (isLgNow() ? setDockOpen : setSheetOpen)(open);
  }, []);
  const [productMode, setProductModeState] = useState<ProductMode>(storedProductMode);
  // The nav follows the same rule as Sources: the lg rail/dock and the phone drawer are separate
  // states, and each reader derives which one applies from its own width (useNavCollapsed).
  const [navLgCollapsed, setNavLgCollapsed] = useState(() =>
    navStartsCollapsed(DESKTOP_MEDIA, storedProductMode()),
  );
  const [navDrawerOpen, setNavDrawerOpen] = useState(false);
  useLayoutEffect(() => {
    if (isLg) setNavDrawerOpen(false);
  }, [isLg]);
  const queueRef = useRef<string[]>([]);
  const drainingRef = useRef(false);
  const groundedTablesRef = useRef(groundedTables);
  const activeSpaceIdRef = useRef(activeSpaceId);
  const sessionIdRef = useRef(sessionId);
  groundedTablesRef.current = groundedTables;
  sessionIdRef.current = sessionId;

  const setGrounded = useCallback((tables: string[], labels: string[] = []) => {
    setGroundedTables(tables);
    setGroundedLabels(labels.length ? labels : tables);
  }, []);

  const setActiveSpaceId = useCallback((id: string | null) => {
    activeSpaceIdRef.current = id;
    setActiveSpaceIdState(id);
  }, []);

  const setProductMode = useCallback((mode: ProductMode) => {
    setProductModeState(mode);
    setNavLgCollapsed(navStartsCollapsed(DESKTOP_MEDIA, mode));
    setNavDrawerOpen(false);
  }, []);

  // Closes the phone drawer. At lg the nav is a dock, which a link tap must leave as it is.
  const closeNavDrawer = useCallback(() => setNavDrawerOpen(false), []);
  const toggleNav = useCallback(() => {
    (isLgNow() ? setNavLgCollapsed : setNavDrawerOpen)((v) => !v);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = productMode;
    window.localStorage.setItem("dms-theme", productMode);
  }, [productMode]);

  useEffect(() => {
    const ctrl = new AbortController();
    const tick = () => {
      void fetchHealth(ctrl.signal)
        .then((body) => {
          if (ctrl.signal.aborted) return;
          setApiOnline(body?.status === "ok");
          if (body?.ask_mode) setAskMode(body.ask_mode);
          if (typeof body?.demo_fallback === "boolean") {
            setDemoFallbackEnabled(body.demo_fallback);
          }
          if (typeof body?.database_configured === "boolean") {
            setDatabaseConfigured(body.database_configured);
          }
          const persistent = body?.database?.persistent;
          if (typeof persistent === "boolean") {
            setSpacesPersisted(persistent);
          } else if (body?.backend === "memory") {
            setSpacesPersisted(false);
          }
          setSpacesStorageHint(body?.database?.hint ?? null);
          const c = body?.dependencies?.cortex;
          const ov = body?.dependencies?.openvault;
          if (c) {
            setCortexContractRoutesOk(c.contract_routes !== false);
            setCortexContractOk(c.ok !== false);
            const refreshOk = c.jwks_refresh?.ok !== false;
            const trustHint =
              c.error ||
              c.jwks_refresh?.hint ||
              ov?.trust?.hint ||
              null;
            setCortexTrustOk(refreshOk && (ov?.trust?.jwks_ok !== false || ov?.ok !== false));
            setCortexTrustHint(trustHint);
          }
        })
        .catch((err: unknown) => {
          if (ctrl.signal.aborted) return;
          if (err instanceof DOMException && err.name === "AbortError") return;
          setApiOnline(false);
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
      .then((body) => {
        if (ctrl.signal.aborted) return;
        const list = body.spaces;
        if (!list.length) return;
        setSpaces(list);
        setSpacesFromApi(true);
        if (body.persisted === false) {
          setSpacesPersisted(false);
        }
        if (body.hint) setSpacesStorageHint(body.hint);
        setActiveSpaceIdState((prev) => {
          // null is Company (default ACL), a real choice. Do not treat it as
          // uninitialized or the first Space fetch snaps the switcher back.
          if (prev === null) return prev;
          if (list.some((s) => s.id === prev)) return prev;
          const next = list[0]?.id ?? null;
          activeSpaceIdRef.current = next;
          return next;
        });
      })
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        setSpacesFromApi(false);
      });
    return () => ctrl.abort();
  }, []);

  const activeSpace = useMemo(
    () => spaces.find((s) => s.id === activeSpaceId) ?? null,
    [spaces, activeSpaceId],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    if (!activeSpaceId) {
      setScoped({ id: null, n: null });
      return () => ctrl.abort();
    }
    void fetchLibrarySources(activeSpaceId, ctrl.signal)
      .then((rows) => setScoped({ id: activeSpaceId, n: rows.length }))
      .catch(() => {
        // An aborted request belongs to a Space or render that is already gone.
        if (ctrl.signal.aborted) return;
        setScoped({ id: activeSpaceId, n: activeSpace?.source_count ?? null });
      });
    return () => ctrl.abort();
  }, [activeSpaceId, activeSpace?.source_count]);

  const scopedSourceCount = scoped.id === activeSpaceId ? scoped.n : null;

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
        setFocusedSourceId(latestAnswer.contributing_sources[0].ref_id);
        // Tapping a figure is an explicit ask for its sources, at every width.
        // (An ask on its own still leaves the phone sheet closed.)
        setSourcePanelOpen(true);
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
    setActivity({ label: "Asking…", progress: null });
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
      const width = typeof window === "undefined" ? LG_MIN_WIDTH_PX : window.innerWidth;
      if (shouldExpandSourcesDock(width)) {
        setSourcePanelOpen(true);
      }
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
    spacesPersisted,
    spacesStorageHint,
    cortexContractOk,
    cortexContractRoutesOk,
    cortexTrustOk,
    cortexTrustHint,
    spaces,
    spacesFromApi,
    activeSpaceId,
    groundedTables,
    groundedLabels,
    setGrounded,
    activeSpace,
    scopedSourceCount,
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
    contributingSources: sourcesForPanel(latestAnswer),
    dockOpen,
    sheetOpen,
    setSourcePanelOpen,
    navLgCollapsed,
    navDrawerOpen,
    toggleNav,
    closeNavDrawer,
    productMode,
    setProductMode,
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp outside AppProvider");
  return ctx;
}

/**
 * Whether Sources is open at the width this component renders at. Every reader derives it from its
 * own useIsLg(), not from a value the provider computed: a media-query listener in a child can
 * commit before the provider's does, and a provider-computed value would then be one width behind.
 */
export function useSourcePanelOpen(): boolean {
  const { dockOpen, sheetOpen } = useApp();
  return useIsLg() ? dockOpen : sheetOpen;
}

/** Whether the nav is collapsed (the rail at lg, the closed drawer below it) at this component's width. */
export function useNavCollapsed(): boolean {
  const { navLgCollapsed, navDrawerOpen } = useApp();
  return useIsLg() ? navLgCollapsed : !navDrawerOpen;
}
