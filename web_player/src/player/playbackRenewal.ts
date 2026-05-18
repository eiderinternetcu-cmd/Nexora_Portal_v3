import type { NexoraClient } from "../api/nexoraClient";

/**
 * Schedules automatic renewal of the short-lived playback JWT (default TTL: 60s).
 *
 * Renewal fires `renewSkewSeconds` before the token expires (default: 15s),
 * giving the player a fresh URL before hls.js needs to re-open a segment request.
 * If renewal fails, it retries in 15 seconds. heartbeatRunner handles auth loss.
 *
 * Phase 4.2 (Signed URLs): once Flussonic backend-auth is active, the renewed
 * playback_url will carry a new ?token=... — call hls.reload() with the new URL.
 */
export class PlaybackRenewal {
  private timer = 0;

  constructor(
    private readonly client: NexoraClient,
    private readonly renewSkewSeconds: number,
  ) {}

  /**
   * Schedule a renewal for the given channel.
   * expiresIn is the TTL in seconds returned by /authorize or /reissue.
   * onRenewed is called with the new playback_url (may be null if not changed).
   */
  schedule(
    channelKey: string,
    expiresIn: number,
    onRenewed: (playbackUrl: string | null | undefined) => void,
  ): void {
    this.cancel();
    const delayMs = Math.max((expiresIn - this.renewSkewSeconds) * 1_000, 5_000);
    this.timer = window.setTimeout(() => {
      void this._doRenew(channelKey, expiresIn, onRenewed);
    }, delayMs);
  }

  cancel(): void {
    if (this.timer) window.clearTimeout(this.timer);
    this.timer = 0;
  }

  private async _doRenew(
    channelKey: string,
    prevExpiresIn: number,
    onRenewed: (playbackUrl: string | null | undefined) => void,
  ): Promise<void> {
    try {
      const response = await this.client.reissuePlayback(channelKey);
      onRenewed(response.playback_url);
      // Schedule the next renewal cycle using the new token's TTL
      this.schedule(channelKey, response.expires_in ?? prevExpiresIn, onRenewed);
    } catch {
      // Retry sooner on error — heartbeatRunner handles full auth loss
      this.timer = window.setTimeout(() => {
        void this._doRenew(channelKey, prevExpiresIn, onRenewed);
      }, 15_000);
    }
  }
}
