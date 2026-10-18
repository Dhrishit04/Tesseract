import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useState } from "react";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { LaunchSequence } from "./components/LaunchSequence";
import { SettingsShell } from "./components/settings/SettingsShell";
import { Sidebar } from "./components/Sidebar";
import { ViewFrame } from "./components/ViewFrame";
import { Workspace } from "./components/Workspace";
import { useChat } from "./hooks/useChat";
import { useChats } from "./hooks/useChats";
import { useSidecarStatus } from "./hooks/useSidecarStatus";
import { calmVariants, useReducedMotionPreference, viewVariants } from "./lib/motion";
import { Route, parseRoute } from "./lib/route";
import { useTheme } from "./lib/theme";
import { isTauri } from "./lib/sidecar";

const NARROW_QUERY = "(max-width: 760px)";

function navigate(path: string, replace = false) {
  const hash = `#${path}`;
  if (replace) window.history.replaceState(null, "", hash);
  else window.location.hash = path;
  if (replace) window.dispatchEvent(new HashChangeEvent("hashchange"));
}

function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

function App() {
  const route = useRoute();
  const sidecar = useSidecarStatus();
  const chats = useChats();
  const reduced = useReducedMotionPreference();
  const theme = useTheme();
  const [sidebarOpen, setSidebarOpen] = useState(() => !window.matchMedia(NARROW_QUERY).matches);

  const activeId = route.view === "chat" ? route.chatId : null;
  const { refresh: refreshChats } = chats;
  const chat = useChat(activeId, {
    ensureChat: chats.create,
    onCreated: (id) => navigate(`/chat/${id}`, true),
    onTurnSaved: () => {
      refreshChats().catch(() => undefined); // refresh reports its own errors in the sidebar
    },
  });

  // "Home" opens the most recent chat, or a fresh one when there are none.
  useEffect(() => {
    if (route.view !== "home" || chats.status === "loading") return;
    navigate(chats.chats[0] ? `/chat/${chats.chats[0].id}` : "/chat/new", true);
  }, [route.view, chats.status, chats.chats]);

  // Re-read the list when the assistant comes back online.
  useEffect(() => {
    if (sidecar.state === "online") refreshChats().catch(() => undefined);
  }, [sidecar.state, refreshChats]);

  // The tray menu's "Settings" item.
  useEffect(() => {
    if (!isTauri()) return;
    let unlisten: (() => void) | undefined;
    import("@tauri-apps/api/event")
      .then(({ listen }) => listen("open-settings", () => navigate("/settings")))
      .then((stop) => {
        unlisten = stop;
      })
      .catch((error) => console.error("Could not listen for tray events", error));
    return () => unlisten?.();
  }, []);

  // On narrow windows the sidebar is an overlay that closes after choosing something.
  useEffect(() => {
    if (window.matchMedia(NARROW_QUERY).matches) setSidebarOpen(false);
  }, [route]);

  const deleteChat = useCallback(
    async (id: string) => {
      const remaining = await chats.remove(id);
      chat.discard(id);
      if (id === activeId) navigate(remaining[0] ? `/chat/${remaining[0].id}` : "/chat/new", true);
    },
    [chats, chat, activeId],
  );

  // Chats share one view so the composer stays put; Workspace animates the conversation itself.
  const viewKey = route.view === "settings" ? "settings" : "chat";
  const variants = reduced ? calmVariants : viewVariants;
  const title = chats.chats.find((item) => item.id === activeId)?.title || "New chat";

  return (
    <div className={`app${sidebarOpen ? "" : " sidebar-collapsed"}`}>
      <LaunchSequence />
      <a className="skip-link" href="#main">Skip to content</a>
      <Sidebar
        chats={chats.chats}
        status={chats.status}
        activeId={activeId}
        settingsOpen={route.view === "settings"}
        query={chats.query}
        sidecar={sidecar.state}
        open={sidebarOpen}
        onQuery={chats.setQuery}
        onNew={() => navigate("/chat/new")}
        onRename={chats.rename}
        onDelete={deleteChat}
        onRetry={chats.retry}
        onClose={() => setSidebarOpen(false)}
      />
      {sidebarOpen && <button type="button" className="sidebar-scrim" aria-label="Close sidebar" onClick={() => setSidebarOpen(false)} />}

      <main id="main" className="main" tabIndex={-1}>
        <AnimatePresence mode="wait" initial={false}>
          <motion.div key={viewKey} className="view" variants={variants} initial="initial" animate="animate" exit="exit">
            <ViewFrame>
            <ErrorBoundary>
              {route.view === "chat" && (
                <Workspace
                  chat={chat}
                  chatId={activeId}
                  title={title}
                  sidecar={sidecar.state}
                  onRetrySidecar={sidecar.retry}
                  onOpenSettings={() => navigate("/settings/models")}
                  onOpenSidebar={() => setSidebarOpen(true)}
                />
              )}
              {route.view === "settings" && (
                <SettingsShell
                  section={route.section}
                  legal={route.legal}
                  sidecar={sidecar.state}
                  theme={theme.preference}
                  onThemeChange={theme.setPreference}
                />
              )}
            </ErrorBoundary>
            </ViewFrame>
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  );
}

export default App;
