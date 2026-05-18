import type { AppConfig } from "../api/config";
import type { Channel, PlaybackResponse } from "../api/types";

export const buildPlaybackUrl = (
  config: AppConfig,
  channel: Channel,
  playback: PlaybackResponse,
) => {
  if (playback.playback_url) return playback.playback_url;

  const template = config.playbackUrlTemplate;
  if (!template) {
    throw new Error(
      `${channel.name}: URL de stream no disponible. Configure source_url en el canal o VITE_NEXORA_PLAYBACK_URL_TEMPLATE.`,
    );
  }
  if (!template.includes("{token}") && !template.includes("{rawToken}")) {
    throw new Error("La plantilla de playback debe incluir {token} o {rawToken}.");
  }

  const replacements: Record<string, string> = {
    token: encodeURIComponent(playback.token),
    rawToken: playback.token,
    channelKey: encodeURIComponent(channel.channel_key),
    channelId: encodeURIComponent(channel.channel_key),
    channelNumber: encodeURIComponent(String(channel.number)),
    subscriberId: encodeURIComponent(playback.subscriber_id),
  };

  return template.replace(/\{(\w+)\}/g, (match, key: string) => {
    return replacements[key] ?? match;
  });
};
