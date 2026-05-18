export type ClientLoginPayload = {
  username: string;
  password?: string;
  activation_code?: string;
  device_id: string;
  device_type?: string;
  model?: string;
  brand?: string;
  app_version?: string;
  os_version?: string;
};

export type ClientTokenResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  subscriber_id: string;
};

export type ClientProfile = {
  subscriber_id: string;
  username: string;
  full_name: string | null;
  email: string | null;
  status: string;
  subscription_expires_at: string | null;
  max_connections: number;
  max_devices: number;
  device_count: number;
  days_remaining: number | null;
};

export type Channel = {
  id: string;
  channel_key: string;
  number: number;
  name: string;
  category: string | null;
  logo_url: string | null;
  requires_subscription: boolean;
};

export type EpgEntry = {
  channel_id: string;
  title: string;
  description: string | null;
  start_at: string;
  end_at: string;
};

export type PlaybackResponse = {
  token: string;
  expires_in: number;
  channel_id: string | null;
  subscriber_id: string;
  playback_url?: string | null;
};

export type HeartbeatResponse = {
  ok?: boolean;
  device_id?: string;
  last_seen?: string;
  subscription_active?: boolean;
  active_connections?: number;
  max_connections?: number;
  expires_at?: string | null;
};

export type LoginInput = {
  username: string;
  password?: string;
  activationCode?: string;
};
