import Hls, { type ErrorData } from "hls.js";

const MAX_NETWORK_RETRIES = 3;
const RETRY_DELAYS_MS = [1_000, 2_000, 4_000];

export type HlsCallbacks = {
  /** Called when all recovery attempts have been exhausted. */
  onFatalError?: (message: string) => void;
  /** Called when a recoverable error triggers an automatic retry. */
  onRetrying?: (attempt: number, maxAttempts: number) => void;
  /** Called when recovery succeeds after a retrying state. */
  onRecovered?: () => void;
};

export class HlsController {
  private hls: Hls | null = null;
  private mediaRecoveryAttempted = false;
  private networkRetryCount = 0;
  private networkRetryTimer = 0;

  constructor(private readonly callbacks: HlsCallbacks = {}) {}

  async load(video: HTMLVideoElement, url: string) {
    this.destroy();
    video.removeAttribute("src");
    video.load();
    this._resetRecovery();

    if (Hls.isSupported()) {
      this.hls = new Hls({
        lowLatencyMode: true,
        backBufferLength: 90,
        liveDurationInfinity: true,
      });
      this.hls.on(Hls.Events.ERROR, (_event, data) => {
        this._handleError(data);
      });
      // Signal recovery after a successful manifest load following a retry
      this.hls.on(Hls.Events.MANIFEST_LOADED, () => {
        if (this.networkRetryCount > 0) {
          this._resetRecovery();
          this.callbacks.onRecovered?.();
        }
      });
      this.hls.attachMedia(video);
      this.hls.loadSource(url);
      await this._playWhenReady(video);
      return;
    }

    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = url;
      await this._playWhenReady(video);
      return;
    }

    throw new Error("Este navegador no soporta HLS.");
  }

  async reload(video: HTMLVideoElement, url: string) {
    this._resetRecovery();
    if (this.hls) {
      this.hls.loadSource(url);
      await this._playWhenReady(video);
      return;
    }
    video.src = url;
    await this._playWhenReady(video);
  }

  destroy() {
    this._clearNetworkRetryTimer();
    this.hls?.destroy();
    this.hls = null;
    this._resetRecovery();
  }

  private _resetRecovery() {
    this.mediaRecoveryAttempted = false;
    this.networkRetryCount = 0;
    this._clearNetworkRetryTimer();
  }

  private _clearNetworkRetryTimer() {
    if (this.networkRetryTimer) {
      window.clearTimeout(this.networkRetryTimer);
      this.networkRetryTimer = 0;
    }
  }

  private _handleError(data: ErrorData) {
    if (!data.fatal) return; // non-fatal: hls.js handles internally via its own retry logic

    if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
      if (!this.mediaRecoveryAttempted) {
        // First media error: attempt graceful codec recovery
        this.mediaRecoveryAttempted = true;
        this.callbacks.onRetrying?.(1, 1);
        this.hls?.recoverMediaError();
        return;
      }
      // Second media error after recovery attempt → truly fatal
      this.callbacks.onFatalError?.(
        "Error de decodificación de video. Intenta cambiar de canal.",
      );
      return;
    }

    if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
      if (this.networkRetryCount < MAX_NETWORK_RETRIES) {
        const attempt = ++this.networkRetryCount;
        const delay = RETRY_DELAYS_MS[attempt - 1] ?? 4_000;
        this.callbacks.onRetrying?.(attempt, MAX_NETWORK_RETRIES);
        this._clearNetworkRetryTimer();
        this.networkRetryTimer = window.setTimeout(() => {
          this.hls?.startLoad();
        }, delay);
        return;
      }
      // Exhausted retries
      this.callbacks.onFatalError?.(
        "Sin señal del servidor de streaming. Verifica tu conexión a internet.",
      );
      return;
    }

    // Other fatal error types (e.g., ErrorTypes.OTHER_ERROR)
    this.callbacks.onFatalError?.("Error en el reproductor. Intenta de nuevo.");
  }

  private _playWhenReady(video: HTMLVideoElement) {
    return new Promise<void>((resolve, reject) => {
      const play = async () => {
        cleanup();
        try {
          await video.play();
          resolve();
        } catch {
          // Autoplay blocked — mute and retry (browser policy)
          video.muted = true;
          try {
            await video.play();
            resolve();
          } catch (error) {
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
