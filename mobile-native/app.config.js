module.exports = ({ config }) => {
  const production = process.env.SMARTLIB_ENV === 'production';
  return {
    ...config,
    android: {
      ...config.android,
      // Cleartext is enabled only for local development; production builds require HTTPS.
      usesCleartextTraffic: !production,
    },
  };
};
