import { KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronLeft,
  Film,
  Grid2X2,
  Maximize,
  Mic2,
  Minimize,
  Pause,
  Pin,
  Play,
  Radio,
  Search,
  Star,
  Tv2,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import type { AppConfig } from "../api/config";
import { messageForError } from "../api/errors";
import { NexoraClient } from "../api/nexoraClient";
import type { Channel } from "../api/types";
import { buildCategories, channelsForCategory } from "../catalog/categories";
import { HeartbeatRunner } from "../heartbeat/heartbeatRunner";
import { HlsController } from "../player/hlsController";
import { buildPlaybackUrl } from "../player/playbackUrl";
import type { ToastTone } from "./ToastHost";

type PlayerViewProps = {
  client: NexoraClient;
  config: AppConfig;
  channels: Channel[];
  initialChannel?: Channel | null;
  onExit: () => void;
  onAuthLost: () => void;
  pushToast: (message: string, tone?: ToastTone) => void;
};

const FAVORITES_KEY = "nexora.web_player.favorites.v1";

const clamp = (value: number, min: number, max: number) =>
  Math.min(Math.max(value, min), max);

export function PlayerView({
  client,
  config,
  channels,
  initialChannel,
  onExit,
  onAuthLost,
  pushToast,
}: PlayerViewProps) {
  const rootRef = useRef<HTMLElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const hlsRef = useRef<HlsController | null>(null);
  const heartbeatRef = useRef<HeartbeatRunner | null>(null);
  const renewTimerRef = useRef(0);
  const idleTimerRef = useRef(0);
  const playSeqRef = useRef(0);
  const autoStartedRef = useRef(false);

  const [current, setCurrent] = useState<Channel | null>(initialChannel ?? null);
  const [activeGenre, setActiveGenre] = useState("");
  const [search, setSearch] = useState("");
  const [focusIndex, setFocusIndex] = useState(0);
  const [sidebarPinned, setSidebarPinned] = useState(true);
  const [dockOpen, setDockOpen] = useState(false);
  const [cinema, setCinema] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("Listo");
  const [playbackError, setPlaybackError] = useState("");
  const [paused, setPaused] = useState(true);
  const [muted, setMuted] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [clock, setClock] = useState(() => formatClock());
  const [expiresAt, setExpiresAt] = useState<number | null>(null);
  const [favorites, setFavorites] = useState<string[]>(() => loadFavorites());

  const categories = useMemo(() => buildCategories(channels), [channels]);
  const genreItems = useMemo(() => {
    const favoriteCount = channels.filter((channel) =>
      favorites.includes(channel.channel_key),
    ).length;
    return [
      { id: "", title: "Todos", count: channels.length },
      { id: "fav", title: "Favoritos", count: favoriteCount },
      ...categories.filter((category) => category.id),
    ];
  }, [categories, channels, favorites]);

  const visibleChannels = useMemo(() => {
    const byGenre =
      activeGenre === "fav"
        ? channels.filter((channel) => favorites.includes(channel.channel_key))
        : channelsForCategory(channels, activeGenre);
    const q = search.trim().toLowerCase();
    if (!q) return byGenre;
    return byGenre.filter((channel) => channel.name.toLowerCase().includes(q));
  }, [activeGenre, channels, favorites, search]);

  useEffect(() => {
    hlsRef.current = new HlsController({
      onFatalError: (message) => {
        setStatus("Error stream");
        setPlaybackError(message);
        pushToast(message, "error");
      },
    });
    return () => {
      window.clearTimeout(renewTimerRef.current);
      window.clearTimeout(idleTimerRef.current);
      heartbeatRef.current?.stop();
      hlsRef.current?.destroy();
    };
  }, [pushToast]);

  useEffect(() => {
    heartbeatRef.current = new HeartbeatRunner(client, config.heartbeatIntervalMs);
    return () => heartbeatRef.current?.stop();
  }, [client, config.heartbeatIntervalMs]);

  useEffect(() => {
    rootRef.current?.focus();
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(formatClock()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const updateFullscreen = () => {
      setFullscreen(Boolean(document.fullscreenElement));
      setCinema(false);
    };
    const activity = () => {
      if (current) {
        setCinema(false);
        queueCinema();
      }
    };
    const events: Array<keyof WindowEventMap> = ["mousemove", "mousedown", "touchstart", "keydown"];
    document.addEventListener("fullscreenchange", updateFullscreen);
    events.forEach((event) => window.addEventListener(event, activity, { passive: true }));
    return () => {
      document.removeEventListener("fullscreenchange", updateFullscreen);
      events.forEach((event) => window.removeEventListener(event, activity));
    };
  }, [current, dockOpen]);

  useEffect(() => {
    if (!initialChannel || autoStartedRef.current) return;
    autoStartedRef.current = true;
    void playChannel(initialChannel);
  });

  useEffect(() => {
    setFocusIndex((index) =>
      visibleChannels.length ? clamp(index, 0, visibleChannels.length - 1) : 0,
    );
  }, [visibleChannels.length]);

  useEffect(() => {
    const item = listRef.current?.querySelector<HTMLElement>(
      `[data-channel-index="${focusIndex}"]`,
    );
    item?.scrollIntoView({ block: "nearest" });
  }, [focusIndex, visibleChannels]);

  useEffect(() => {
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(favorites));
  }, [favorites]);

  function queueCinema() {
    window.clearTimeout(idleTimerRef.current);
    idleTimerRef.current = window.setTimeout(() => {
      if (current && !dockOpen) setCinema(true);
    }, 4200);
  }

  function scheduleRenew(channel: Channel, expiresIn: number) {
    window.clearTimeout(renewTimerRef.current);
    setExpiresAt(Date.now() + expiresIn * 1000);
    const renewMs = Math.max(
      5_000,
      (expiresIn - config.playbackRenewSkewSeconds) * 1000,
    );
    renewTimerRef.current = window.setTimeout(() => {
      void renewPlayback(channel);
    }, renewMs);
  }

  async function renewPlayback(channel: Channel) {
    const video = videoRef.current;
    if (!video || current?.channel_key !== channel.channel_key) return;

    try {
      const playback = await client.reissuePlayback(channel.channel_key);
      const url = buildPlaybackUrl(config, channel, playback);
      await hlsRef.current?.reload(video, url);
      scheduleRenew(channel, playback.expires_in);
      setStatus("En vivo");
    } catch {
      try {
        const playback = await client.authorizePlayback(channel.channel_key);
        const url = buildPlaybackUrl(config, channel, playback);
        await hlsRef.current?.reload(video, url);
        scheduleRenew(channel, playback.expires_in);
        setStatus("En vivo");
      } catch (fallbackError) {
        const message = messageForError(fallbackError);
        setStatus("Error");
        setPlaybackError(message);
        pushToast(message, "error");
      }
    }
  }

  async function playChannel(channel: Channel) {
    const video = videoRef.current;
    if (!video) return;

    const seq = ++playSeqRef.current;
    setCurrent(channel);
    setLoading(true);
    setCinema(false);
    setDockOpen(false);
    setStatus("Obteniendo enlace");
    setPlaybackError("");
    setFocusIndex(Math.max(0, visibleChannels.findIndex((item) => item.channel_key === channel.channel_key)));
    pushToast(`Cargando ${channel.name}...`);

    try {
      const playback = await client.authorizePlayback(channel.channel_key);
      const url = buildPlaybackUrl(config, channel, playback);
      if (seq !== playSeqRef.current) return;
      setStatus("Conectando");
      await hlsRef.current?.load(video, url);
      if (seq !== playSeqRef.current) return;
      setLoading(false);
      setPaused(video.paused);
      setMuted(video.muted);
      setStatus("En vivo");
      scheduleRenew(channel, playback.expires_in);
      startPlaybackHeartbeat();
      queueCinema();
      pushToast(`Conectado: ${channel.name}`, "success");
    } catch (error) {
      if (seq !== playSeqRef.current) return;
      const message = messageForError(error);
      if (message.includes("Sesion vencida")) onAuthLost();
      setLoading(false);
      setStatus("Error");
      setPlaybackError(message);
      pushToast(message, "error");
    }
  }

  function startPlaybackHeartbeat() {
    heartbeatRef.current?.start({
      onAuthLost,
      onStatus: (message) => pushToast(message, "error"),
    });
  }

  function playRelative(step: number) {
    if (!visibleChannels.length) return;
    const currentIndex = current
      ? visibleChannels.findIndex((channel) => channel.channel_key === current.channel_key)
      : -1;
    const nextIndex =
      currentIndex < 0
        ? 0
        : (currentIndex + step + visibleChannels.length) % visibleChannels.length;
    setFocusIndex(nextIndex);
    void playChannel(visibleChannels[nextIndex]);
  }

  function toggleFavorite(channel: Channel) {
    setFavorites((items) =>
      items.includes(channel.channel_key)
        ? items.filter((key) => key !== channel.channel_key)
        : [...items, channel.channel_key],
    );
  }

  function togglePlayPause() {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      void video.play().then(() => setPaused(false));
    } else {
      video.pause();
      setPaused(true);
    }
  }

  function toggleMute() {
    const video = videoRef.current;
    if (!video) return;
    video.muted = !video.muted;
    if (!video.muted) video.volume = 1;
    setMuted(video.muted);
  }

  async function toggleFullscreen() {
    if (!document.fullscreenElement) {
      await rootRef.current?.requestFullscreen();
    } else {
      await document.exitFullscreen();
    }
  }

  function handleKey(event: KeyboardEvent<HTMLElement>) {
    if (dockOpen && event.key === "Escape") {
      event.preventDefault();
      setDockOpen(false);
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      setSidebarPinned(true);
      setFocusIndex((index) => clamp(index - 1, 0, Math.max(visibleChannels.length - 1, 0)));
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSidebarPinned(true);
      setFocusIndex((index) => clamp(index + 1, 0, Math.max(visibleChannels.length - 1, 0)));
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (sidebarPinned) playRelative(-1);
      else setSidebarPinned(true);
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      if (sidebarPinned) setSidebarPinned(false);
      else playRelative(1);
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const selected = visibleChannels[focusIndex];
      if (selected) void playChannel(selected);
    }
    if (event.key === " " || event.key === "MediaPlayPause") {
      event.preventDefault();
      togglePlayPause();
    }
    if (event.key === "Escape" || event.key === "Backspace") {
      event.preventDefault();
      if (cinema) setCinema(false);
      else onExit();
    }
    if (event.key.toLowerCase() === "f") {
      event.preventDefault();
      void toggleFullscreen();
    }
    if (event.key.toLowerCase() === "m") {
      event.preventDefault();
      toggleMute();
    }
  }

  const currentFavorite = current ? favorites.includes(current.channel_key) : false;
  const expirySeconds =
    expiresAt === null ? null : Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));

  return (
    <main
      ref={rootRef}
      className={`screen player-pro ${sidebarPinned ? "sidebar-pinned" : ""} ${cinema ? "is-cinema" : ""}`}
      tabIndex={0}
      onKeyDown={handleKey}
      onPointerDown={() => rootRef.current?.focus()}
    >
      <div className="pro-video-bg">
        <video
          ref={videoRef}
          className="pro-video"
          playsInline
          autoPlay
          onClick={togglePlayPause}
          onPlaying={() => {
            setLoading(false);
            setPaused(false);
            setMuted(Boolean(videoRef.current?.muted));
            setStatus("En vivo");
          }}
          onPause={() => setPaused(true)}
          onWaiting={() => setStatus("Buffering")}
          onVolumeChange={() => setMuted(Boolean(videoRef.current?.muted))}
          onError={() => {
            setStatus("Error de reproduccion");
            setLoading(false);
          }}
        />
      </div>

      <div className="pro-shell">
        <header className="pro-header">
          <button className="pro-icon" type="button" aria-label="Volver al inicio" onClick={onExit}>
            <ChevronLeft size={20} />
          </button>
          <button className="pro-brand" type="button" onClick={onExit} aria-label="NEXORA">
            <img src="/assets/player-logo.png" alt="" />
            <span>
              <strong>NEXORA</strong>
              <small>Player Pro</small>
            </span>
          </button>
          <nav className="pro-nav" aria-label="Secciones">
            <button className="active" type="button">
              <Tv2 size={16} />
              En Vivo
            </button>
            <button type="button" onClick={() => pushToast("VOD disponible pronto.")}>
              <Film size={16} />
              VOD
            </button>
            <button type="button" onClick={() => pushToast("Karaoke disponible pronto.")}>
              <Mic2 size={16} />
              Karaoke
            </button>
            <button type="button" onClick={() => pushToast("Radio disponible pronto.")}>
              <Radio size={16} />
              Radio
            </button>
          </nav>
          <span className="pro-count">{channels.length} Canales</span>
          <div className="pro-header-actions">
            <button
              className={`pro-icon ${sidebarPinned ? "active" : ""}`}
              type="button"
              aria-label="Fijar canales"
              onClick={() => setSidebarPinned((value) => !value)}
            >
              <Pin size={18} />
            </button>
            <button className="pro-icon" type="button" aria-label="Mosaico" onClick={() => setDockOpen(true)}>
              <Grid2X2 size={18} />
            </button>
            <button
              className={`pro-icon ${cinema ? "active" : ""}`}
              type="button"
              aria-label="Modo cine"
              onClick={() => setCinema(true)}
            >
              <Maximize size={18} />
            </button>
          </div>
        </header>

        <aside className="pro-sidebar" aria-label="Canales">
          <div className="pro-sidebar-inner">
            <div className="pro-sidebar-head">
              <button className="pro-icon" type="button" aria-label="Mosaico" onClick={() => setDockOpen(true)}>
                <Grid2X2 size={18} />
              </button>
              <div className="pro-sidebar-title">
                <strong>Canales</strong>
                <span>{visibleChannels.length} disponibles</span>
              </div>
            </div>

            <label className="pro-search">
              <Search size={16} />
              <input
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setFocusIndex(0);
                }}
                placeholder="Buscar canal..."
                autoComplete="off"
              />
            </label>

            <div className="pro-genre-bar" aria-label="Categorias">
              {genreItems.map((item) => (
                <button
                  key={item.id || "all"}
                  className={activeGenre === item.id ? "active" : ""}
                  type="button"
                  onClick={() => {
                    setActiveGenre(item.id);
                    setFocusIndex(0);
                  }}
                >
                  {item.title}
                </button>
              ))}
            </div>

            <div className="pro-channel-list" ref={listRef}>
              {visibleChannels.length ? (
                visibleChannels.map((channel, index) => {
                  const active = current?.channel_key === channel.channel_key;
                  const focused = index === focusIndex;
                  const fav = favorites.includes(channel.channel_key);
                  return (
                    <button
                      key={channel.channel_key}
                      type="button"
                      data-channel-index={index}
                      className={`pro-channel-item ${active ? "active" : ""} ${focused ? "focused" : ""}`}
                      data-label={channel.name}
                      onClick={() => {
                        setFocusIndex(index);
                        void playChannel(channel);
                      }}
                    >
                      <ChannelLogo channel={channel} active={active || focused} />
                      <span className="pro-channel-meta">
                        <strong>{channel.name}</strong>
                        <small>Canal {channel.number}</small>
                      </span>
                      <span
                        className={`pro-fav-toggle ${fav ? "active" : ""}`}
                        role="button"
                        tabIndex={-1}
                        onClick={(event) => {
                          event.stopPropagation();
                          toggleFavorite(channel);
                        }}
                      >
                        <Star size={16} />
                      </span>
                    </button>
                  );
                })
              ) : (
                <div className="pro-empty">Sin resultados</div>
              )}
            </div>
          </div>
        </aside>

        {!current && (
          <section className="pro-placeholder" aria-label="NEXORA">
            <div className="pro-hero">
              <div className="pro-hero-mark">
                <img src="/assets/player-logo.png" alt="" />
              </div>
              <div className="pro-hero-copy">
                <div className="pro-hero-kicker">En Vivo</div>
                <h1>NEXORA</h1>
                <p>Tu sala de mando para TV, VOD, karaoke y radio con una experiencia inmersiva de pantalla completa.</p>
                <div className="pro-hero-actions">
                  <button type="button" className="primary" onClick={() => visibleChannels[0] && void playChannel(visibleChannels[0])}>
                    Reproducir
                  </button>
                  <button type="button" onClick={() => setSidebarPinned(true)}>
                    Buscar canal
                  </button>
                  <button type="button" onClick={() => setDockOpen(true)}>
                    Ver mosaico
                  </button>
                </div>
              </div>
            </div>
          </section>
        )}

        {loading && <div className="pro-loading"><span />Conectando stream...</div>}

        {paused && current && (
          <button className="pro-stage-play" type="button" aria-label="Reproducir" onClick={togglePlayPause}>
            <Play size={34} fill="currentColor" />
          </button>
        )}

        {playbackError && (
          <div className="pro-playback-error">{playbackError}</div>
        )}

        <footer className="pro-now-playing">
          <div className="pro-np-left">
            <div className="pro-np-orb" />
            <div className="pro-np-info">
              <strong>{current?.name ?? "NEXORA Player Pro"}</strong>
              <small>
                <span>En Vivo</span>
                {status}
                {expirySeconds !== null && status === "En vivo" ? ` - ${expirySeconds}s` : ""}
              </small>
            </div>
          </div>

          <div className="pro-np-controls">
            <button className="pro-icon" type="button" aria-label="Canal anterior" onClick={() => playRelative(-1)}>
              <ChevronLeft size={19} />
            </button>
            <button className="pro-icon primary" type="button" aria-label="Pausar/Reproducir" onClick={togglePlayPause}>
              {paused ? <Play size={19} fill="currentColor" /> : <Pause size={19} fill="currentColor" />}
            </button>
            <button className="pro-icon" type="button" aria-label="Siguiente canal" onClick={() => playRelative(1)}>
              <ChevronLeft className="flip-x" size={19} />
            </button>
          </div>

          <div className="pro-np-actions">
            <button
              className={`pro-icon ${currentFavorite ? "active-fav" : ""}`}
              type="button"
              aria-label="Favorito"
              disabled={!current}
              onClick={() => current && toggleFavorite(current)}
            >
              <Star size={18} fill={currentFavorite ? "currentColor" : "none"} />
            </button>
            <button className="pro-icon" type="button" aria-label="Mosaico" onClick={() => setDockOpen(true)}>
              <Grid2X2 size={18} />
            </button>
            <button className="pro-icon" type="button" aria-label="Audio" onClick={toggleMute}>
              {muted ? <VolumeX size={18} /> : <Volume2 size={18} />}
            </button>
            <button className="pro-icon" type="button" aria-label="Pantalla completa" onClick={() => void toggleFullscreen()}>
              {fullscreen ? <Minimize size={18} /> : <Maximize size={18} />}
            </button>
          </div>
        </footer>
      </div>

      <section className={`pro-dock ${dockOpen ? "open" : ""}`} aria-label="Mosaico de canales">
        <div className="pro-dock-head">
          <div>
            <strong>Mosaico de canales</strong>
            <span>{visibleChannels.length} canales</span>
          </div>
          <button className="pro-icon" type="button" aria-label="Cerrar mosaico" onClick={() => setDockOpen(false)}>
            <X size={19} />
          </button>
        </div>
        <div className="pro-grid-list">
          {visibleChannels.map((channel) => (
            <button
              key={channel.channel_key}
              type="button"
              className={current?.channel_key === channel.channel_key ? "active" : ""}
              onClick={() => void playChannel(channel)}
            >
              <ChannelLogo channel={channel} active={current?.channel_key === channel.channel_key} />
              <span>{channel.name}</span>
            </button>
          ))}
        </div>
      </section>
      <div className="pro-clock">{clock}</div>
    </main>
  );
}

function ChannelLogo({ channel, active }: { channel: Channel; active: boolean }) {
  const [failed, setFailed] = useState(false);
  const src = channel.logo_url?.trim();

  return (
    <span className={`pro-channel-logo ${active ? "active" : ""}`}>
      {src && !failed ? (
        <img src={src} alt="" onError={() => setFailed(true)} />
      ) : (
        <Play size={18} fill="currentColor" />
      )}
    </span>
  );
}

function loadFavorites() {
  try {
    const raw = localStorage.getItem(FAVORITES_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function formatClock() {
  const now = new Date();
  return `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
}
