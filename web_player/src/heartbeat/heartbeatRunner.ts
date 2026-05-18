import { ApiError } from "../api/errors";
import { NexoraClient } from "../api/nexoraClient";

export type HeartbeatCallbacks = {
  onStatus?: (message: string) => void;
  onAuthLost?: () => void;
  onError?: (error: unknown) => void;
};

export class HeartbeatRunner {
  private timer = 0;
  private inFlight = false;

  constructor(
    private readonly client: NexoraClient,
    private readonly intervalMs: number,
  ) {}

  start(callbacks: HeartbeatCallbacks = {}) {
    this.stop();
    void this.tick(callbacks);
    this.timer = window.setInterval(() => {
      void this.tick(callbacks);
    }, this.intervalMs);
  }

  stop() {
    if (this.timer) window.clearInterval(this.timer);
    this.timer = 0;
    this.inFlight = false;
  }

  private async tick(callbacks: HeartbeatCallbacks) {
    if (this.inFlight) return;
    this.inFlight = true;
    try {
      const heartbeat = await this.client.heartbeat();
      if (heartbeat.subscription_active === false) {
        callbacks.onStatus?.("Suscripcion inactiva.");
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        callbacks.onAuthLost?.();
      } else {
        callbacks.onError?.(error);
      }
    } finally {
      this.inFlight = false;
    }
  }
}
