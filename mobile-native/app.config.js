module.exports = ({ config }) => {
  const production = process.env.SMARTLIB_ENV === 'production';
  const api = process.env.EXPO_PUBLIC_API_BASE_URL || '';
  if (production) {
    let url;
    try { url = new URL(api); } catch { /* handled below */ }
    if (!url || url.protocol !== 'https:' || url.username || url.password || url.search || url.hash || url.pathname !== '/' ||
        !url.hostname.includes('.') || url.hostname.includes(':') || /^(localhost|0\.|127\.|10\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(url.hostname) ||
        /\.(local|internal|invalid|example|test)$/.test(url.hostname) || ['example.com', 'example.org', 'example.net'].includes(url.hostname)) {
      throw new Error('Release builds require EXPO_PUBLIC_API_BASE_URL to be your deployed public HTTPS backend URL.');
    }
  }
  const iosInfo = { ...(config.ios?.infoPlist || {}) };
  if (production) {
    delete iosInfo.NSLocalNetworkUsageDescription;
    delete iosInfo.NSAppTransportSecurity;
    iosInfo.ITSAppUsesNonExemptEncryption = false;
  }
  return {
    ...config,
    plugins: [...(config.plugins || []), '@react-native-community/datetimepicker'],
    ios: { ...config.ios, infoPlist: iosInfo },
    android: {
      ...config.android,
      // Cleartext is enabled only for local development; production builds require HTTPS.
      usesCleartextTraffic: !production,
    },
  };
};
