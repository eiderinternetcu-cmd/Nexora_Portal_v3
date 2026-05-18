import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { appConfig } from "./api/config";
import { messageForError } from "./api/errors";
import { NexoraClient } from "./api/nexoraClient";
import type { Channel, ClientProfile } from "./api/types";
import { TokenStore } from "./auth/tokenStore";
import { HomeView } from "./ui/HomeView";
import { LoginView } from "./ui/LoginView";
import { NexoraBrand } from "./ui/NexoraBrand";
import { PlayerView } from "./ui/PlayerView";
import { ToastHost, type Toast, type ToastTone } from "./ui/ToastHost";

type View = "boot" | "login" | "home" | "player";

export function App() {
  const store = useMemo(() => new TokenStore(), []);
  const client = useMemo(() => new NexoraClient(appConfig, store), [store]);

  const toastIdRef = useRef(0);
  const [view, setView] = useState<View>("boot");
  const [profile, setProfile] = useState<ClientProfile | null>(null);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loadingData, setLoadingData] = useState(false);
  const [initialChannel, setInitialChannel] = useState<Channel | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);

  const pushToast = useCallback((message: string, tone: ToastTone = "info") => {
    const id = ++toastIdRef.current;
    setToasts((items) => [...items, { id, message, tone }]);
    window.setTimeout(() => {
      setToasts((items) => items.filter((item) => item.id !== id));
    }, 3400);
  }, []);

  const handleAuthLost = useCallback(() => {
    store.clear();
    setProfile(null);
    setChannels([]);
    setInitialChannel(null);
    setView("login");
    pushToast("Sesion vencida.", "error");
  }, [pushToast, store]);

  const loadPortalData = useCallback(async () => {
    setLoadingData(true);
    try {
      const [nextProfile, nextChannels] = await Promise.all([
        client.getProfile(),
        client.getChannels(),
      ]);
      setProfile(nextProfile);
      setChannels(nextChannels);
      return { profile: nextProfile, channels: nextChannels };
    } finally {
      setLoadingData(false);
    }
  }, [client]);

  useEffect(() => {
    let cancelled = false;

    const boot = async () => {
      if (!client.currentSession()) {
        setView("login");
        return;
      }

      try {
        await loadPortalData();
        if (cancelled) return;
        setView("home");
      } catch (error) {
        if (cancelled) return;
        store.clear();
        setView("login");
        pushToast(messageForError(error), "error");
      }
    };

    void boot();
    return () => {
      cancelled = true;
    };
  }, [client, loadPortalData, pushToast, store]);

  const login = async (username: string, password?: string, activationCode?: string) => {
    await client.login({ username, password, activationCode });
    await loadPortalData();
    setView("home");
    pushToast("Sesion iniciada.", "success");
  };

  const logout = async () => {
    await client.logout();
    setProfile(null);
    setChannels([]);
    setInitialChannel(null);
    setView("login");
  };

  const refresh = async () => {
    try {
      await loadPortalData();
      pushToast("Catalogo actualizado.", "success");
    } catch (error) {
      pushToast(messageForError(error), "error");
    }
  };

  const openPlayer = async () => {
    if (!channels.length) {
      try {
        const loaded = await loadPortalData();
        setInitialChannel(loaded.channels[0] ?? null);
      } catch (error) {
        pushToast(messageForError(error), "error");
        return;
      }
    } else {
      setInitialChannel(channels[0] ?? null);
    }
    setView("player");
  };

  return (
    <>
      {view === "boot" && <BootScreen />}
      {view === "login" && <LoginView onLogin={login} />}
      {view === "home" && profile && (
        <HomeView
          profile={profile}
          channels={channels}
          loading={loadingData}
          onOpenLive={openPlayer}
          onRefresh={refresh}
          onLogout={() => void logout()}
          onUnavailable={(label) => pushToast(`${label} disponible pronto.`)}
        />
      )}
      {view === "player" && (
        <PlayerView
          client={client}
          config={appConfig}
          channels={channels}
          initialChannel={initialChannel}
          onExit={() => setView("home")}
          onAuthLost={handleAuthLost}
          pushToast={pushToast}
        />
      )}
      <ToastHost toasts={toasts} />
    </>
  );
}

function BootScreen() {
  return (
    <main className="screen boot-screen">
      <div className="bg-layer" />
      <div className="boot-brand">
        <NexoraBrand />
        <div className="boot-progress" />
      </div>
    </main>
  );
}
