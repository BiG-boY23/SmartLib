# EVSU SmartLib Mobile (React Native)

This is the native iOS and Android patron client for the existing FastAPI SmartLib service. It uses Expo SDK 57, React Native 0.86, and React 19.2.3.

## Start the backend for a phone

From the project root, start FastAPI so another device on the same Wi-Fi can connect:

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

For development, you can enter the computer’s Wi-Fi IP and port 8000 on the sign-in screen; the same setting is available under Profile. Android emulators default to `http://10.0.2.2:8000`. For a fixed setup or production build, copy `.env.example` to `.env` and set `EXPO_PUBLIC_API_BASE_URL`. Use HTTPS for deployed builds. Expo public variables are bundled into the app, so they are configuration values and must not contain secrets.

## Run the native app

Install Node.js 22.13 or newer, copy `.env.example` to `.env`, set the API URL, then from this folder:

```powershell
npm.cmd install
npx.cmd expo start
```

In PowerShell, use `npm.cmd install` and `npx.cmd expo start` if script execution blocks `npm.ps1` or `npx.ps1`. Expo Go on Android should support SDK 57; scan the development QR code after starting Expo from this folder. For an iOS simulator, use macOS with Xcode and run `npx expo start --ios`.

## Patron features

- Sign in, or create a student or faculty account.
- Confirm the password during sign-up; new accounts must verify their email before sign-in. Sign-in also includes Remember me and Forgot password.
- Sign out after 15 minutes without activity; the API independently expires idle sessions.
- Search the shared catalog and request available books.
- Review borrow request status and due dates. Local return reminders run in a development or production build; the due-date tracker still works in Expo Go.
- Display the digital library ID QR code.
- Raise screen brightness while the Library ID tab is open, then restore the previous level when leaving it.
- Send a book acquisition suggestion.
- Keep the API token in platform secure storage only when Remember me is selected.

For production Android builds, set `SMARTLIB_ENV=production` and `EXPO_PUBLIC_API_BASE_URL` in the build environment. Remote push notifications require a development/production build and the platform push credentials. Expo Go on Android skips loading the notification module to avoid the SDK 57 remote-push import crash.
