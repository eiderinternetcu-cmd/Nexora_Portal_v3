import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.nexora.play',
  appName: 'Nexora Play',
  webDir: 'dist',
  server: {
    androidScheme: 'https',
    hostname: 'nexoraplay.net',
    cleartext: true,
  },
};

export default config;
