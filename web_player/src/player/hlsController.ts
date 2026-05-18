import Hls from "hls.js";

type HlsCallbacks = {
  onFatalError?: (message: string) => void;
};

export class HlsController {
  private hls: Hls | null = null;

  constructor(private readonly callbacks: HlsCallbacks = {}) {}

  async load(video: HTMLVideoElement, url: string) {
    this.destroy();
    video.removeAttribute("src");
    video.load();

    if (Hls.isSupported()) {
      this.hls = new Hls({
        lowLatencyMode: true,
        backBufferLength: 90,
        liveDurationInfinity: true,
      });
      this.hls.on(Hls.Events.ERROR, (_event, data) => {
        if (!data.fatal) return;
        if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
          this.hls?.startLoad();
          this.callbacks.onFatalError?.("Error de red en el stream.");
          return;
        }
        this.callbacks.onFatalError?.("No se pudo reproducir el stream.");
      });
      this.hls.attachMedia(video);
      this.hls.loadSource(url);
      await this.playWhenReady(video);
      return;
    }

    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = url;
      await this.playWhenReady(video);
      return;
    }

    throw new Error("Este navegador no soporta HLS.");
  }

  async reload(video: HTMLVideoElement, url: string) {
    if (this.hls) {
      this.hls.loadSource(url);
      await this.playWhenReady(video);
      return;
    }
    video.src = url;
    await this.playWhenReady(video);
  }

  destroy() {
    this.hls?.destroy();
    this.hls = null;
  }

  private playWhenReady(video: HTMLVideoElement) {
    return new Promise<void>((resolve, reject) => {
      const play = async () => {
        cleanup();
        try {
          await video.play();
          resolve();
        } catch (error) {
          video.muted = true;
          try {
            await video.play();
            resolve();
          } catch {
            reject(error);
          }
        }
      };
      const fail = () => {
        cleanup();
        reject(new Error("No se pudo iniciar el video."));
      };
      const cleanup = () => {
        video.removeEventListener("canplay", play);
        video.removeEventListener("error", fail);
      };

      if (video.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA) {
        void play();
        return;
      }
      video.addEventListener("canplay", play, { once: true });
      video.addEventListener("error", fail, { once: true });
    });
  }
}
