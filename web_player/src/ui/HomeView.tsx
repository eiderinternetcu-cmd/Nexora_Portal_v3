import { useState } from "react";
import type { ReactNode } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Film,
  Laptop,
  LogOut,
  Mic2,
  Monitor,
  Play,
  Radio,
  RefreshCw,
  Smartphone,
  Tablet,
  Tv2,
} from "lucide-react";
import type { Channel, ClientProfile } from "../api/types";

type HomeViewProps = {
  profile: ClientProfile;
  channels: Channel[];
  loading: boolean;
  onOpenLive: () => void;
  onRefresh: () => void;
  onLogout: () => void;
  onUnavailable: (label: string) => void;
};

type Poster = {
  title: string;
  meta: string;
  image: string;
  bg: string;
};

const posters: Poster[] = [
  {
    title: "Interstellar",
    meta: "2014 - Sci-Fi",
    image: "https://image.tmdb.org/t/p/w500/gEU2QniE6E77NI6lCU6MxlNBvIe.jpg",
    bg: "linear-gradient(160deg,#0a1a3a,#1a3060)",
  },
  {
    title: "Dune: Part One",
    meta: "2021 - Sci-Fi",
    image: "https://image.tmdb.org/t/p/w500/d5NXSklpcKDIyjpyTi0Yn0IQWEA.jpg",
    bg: "linear-gradient(160deg,#2a1400,#5a2e00)",
  },
  {
    title: "Oppenheimer",
    meta: "2023 - Drama",
    image: "https://image.tmdb.org/t/p/w500/8Gxv8gSFCU0XGDykEGv7zR1n2ua.jpg",
    bg: "linear-gradient(160deg,#1a0800,#4a1500)",
  },
  {
    title: "Avatar 2",
    meta: "2022 - Accion",
    image: "https://image.tmdb.org/t/p/w500/t6HIqrRAclMCA60NsSmeqe9RmIE.jpg",
    bg: "linear-gradient(160deg,#001a2a,#004060)",
  },
  {
    title: "The Batman",
    meta: "2022 - Accion",
    image: "https://image.tmdb.org/t/p/w500/74xTEgt7R36Fpooo50r9T25onhq.jpg",
    bg: "linear-gradient(160deg,#0a0a18,#1a1a35)",
  },
  {
    title: "Spider-Man: NWH",
    meta: "2021 - Accion",
    image: "https://image.tmdb.org/t/p/w500/1g0dhYtq4irTY1GPXvft6k4YLjm.jpg",
    bg: "linear-gradient(160deg,#0d1a10,#1a3520)",
  },
  {
    title: "Top Gun: Maverick",
    meta: "2022 - Accion",
    image: "https://image.tmdb.org/t/p/w500/62HCnUTziyWcpDaBO2i1DX17ljH.jpg",
    bg: "linear-gradient(160deg,#0a1520,#0a2a40)",
  },
  {
    title: "Dune: Part Two",
    meta: "2024 - Sci-Fi",
    image: "https://image.tmdb.org/t/p/w500/cdqLnri3NEGcmfnqwk2TSIYtddg.jpg",
    bg: "linear-gradient(160deg,#1a1000,#3a2800)",
  },
  {
    title: "Barbie",
    meta: "2023 - Comedia",
    image: "https://image.tmdb.org/t/p/w500/iuFNMS8U5cb6xfzi51Dbkovj7vM.jpg",
    bg: "linear-gradient(160deg,#5a0040,#cc0088)",
  },
  {
    title: "Gladiator II",
    meta: "2024 - Accion",
    image: "https://image.tmdb.org/t/p/w500/2cxhvwyE0RwinBZ3LMnGMPKWMFj.jpg",
    bg: "linear-gradient(160deg,#1a0505,#3a0f0f)",
  },
];

const deviceIcons = [Monitor, Laptop, Tablet, Smartphone, Tv2];

export function HomeView({
  profile,
  channels,
  loading,
  onOpenLive,
  onRefresh,
  onLogout,
  onUnavailable,
}: HomeViewProps) {
  const [active, setActive] = useState<number | null>(null);

  const move = (delta: number) => {
    setActive((value) => {
      const current = value ?? 0;
      return (current + delta + posters.length) % posters.length;
    });
  };

  const displayName = profile.full_name || profile.username;

  return (
    <main
      className="screen launcher-pro"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === "Enter" && event.target !== event.currentTarget) return;
        if (event.key === "ArrowLeft") move(-1);
        if (event.key === "ArrowRight") move(1);
        if (event.key === "Enter") onOpenLive();
        if (event.key === "Escape") onLogout();
      }}
    >
      <div className="launcher-bg" />
      <div className="launcher-cosmos" aria-hidden="true">
        <span className="nebula n1" />
        <span className="nebula n2" />
        <span className="nebula n3" />
        {Array.from({ length: 22 }).map((_, index) => (
          <span key={index} className={`star s${(index % 8) + 1}`} />
        ))}
      </div>

      <div className="launcher-session">
        <span>{displayName}</span>
        <strong>{profile.days_remaining ?? 0} dias</strong>
        <button type="button" aria-label="Actualizar" onClick={onRefresh}>
          <RefreshCw size={16} />
        </button>
        <button type="button" aria-label="Salir" onClick={onLogout}>
          <LogOut size={16} />
        </button>
      </div>

      <section className="launcher-top">
        <div className="launcher-logo">
          <img src="/assets/player-logo.png" alt="" />
        </div>
        <div className="launcher-brand">NEXORA</div>
      </section>

      <section className="launcher-cards">
        <span className="launcher-label">
          <strong>VOD</strong> - Peliculas destacadas
        </span>

        <div className="launcher-scene">
          <div className="launcher-fade-left" />
          <div className={`launcher-track ${active !== null ? "has-active" : ""}`}>
            {posters.map((poster, index) => (
              <button
                key={poster.title}
                type="button"
                className={`launcher-card ${active === index ? "active" : ""}`}
                style={{ background: poster.bg }}
                onClick={() => setActive((value) => (value === index ? null : index))}
              >
                <img src={poster.image} alt={poster.title} />
                <span className="launcher-card-tint" />
                <span className="launcher-card-info">
                  <strong>{poster.title}</strong>
                  <small>{poster.meta}</small>
                </span>
              </button>
            ))}
          </div>
          <div className="launcher-fade-right" />

          <button className="launcher-arrow prev" type="button" aria-label="Anterior" onClick={() => move(-1)}>
            <ChevronLeft size={16} />
          </button>
          <button className="launcher-arrow next" type="button" aria-label="Siguiente" onClick={() => move(1)}>
            <ChevronRight size={16} />
          </button>

          <div className="launcher-dots" aria-label="Peliculas">
            {posters.map((poster, index) => (
              <button
                key={poster.title}
                type="button"
                className={active === index ? "active" : ""}
                aria-label={poster.title}
                onClick={() => setActive(index)}
              />
            ))}
          </div>
        </div>
      </section>

      <footer className="launcher-footer">
        <div className="launcher-devices">
          <div className="launcher-device-icons">
            {deviceIcons.map((Icon, index) => (
              <span key={index} className="launcher-device-icon">
                <Icon size={22} />
              </span>
            ))}
          </div>
          <span>Disponible en todos tus dispositivos</span>
        </div>

        <div className="launcher-divider" />

        <div className="launcher-footer-main">
          <button className="launcher-play" type="button" onClick={onOpenLive} aria-label="Reproducir">
            <Play size={22} fill="currentColor" />
          </button>

          <div className="launcher-features">
            <button type="button" className="launcher-feature" onClick={onOpenLive}>
              <FeatureIcon icon={<Tv2 size={23} />} />
              <span>Canales en Vivo</span>
              <small>{loading ? "Cargando" : `${channels.length} canales`}</small>
            </button>
            <button type="button" className="launcher-feature" onClick={() => onUnavailable("VOD")}>
              <FeatureIcon icon={<Film size={23} />} />
              <span>VOD</span>
              <small>Peliculas y series</small>
            </button>
            <button type="button" className="launcher-feature" onClick={() => onUnavailable("Karaoke")}>
              <FeatureIcon icon={<Mic2 size={23} />} />
              <span>Karaoke</span>
              <small>Miles de canciones</small>
            </button>
            <button type="button" className="launcher-feature" onClick={() => onUnavailable("Radio")}>
              <FeatureIcon icon={<Radio size={23} />} />
              <span>Radio</span>
              <small>Emisoras en vivo</small>
            </button>
          </div>
        </div>

        <div className="launcher-footer-bottom">
          <div>NEXORA.IO</div>
          <span>(C) 2026 Nexora - Todos los derechos reservados</span>
        </div>
      </footer>
    </main>
  );
}

function FeatureIcon({ icon }: { icon: ReactNode }) {
  return <span className="launcher-feature-icon">{icon}</span>;
}
