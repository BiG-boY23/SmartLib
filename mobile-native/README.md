# EVSU SmartLib Mobile (React Native)

This is the native iOS and Android patron client for the existing FastAPI SmartLib service. It uses Expo and React Native, with the current stable Expo SDK pairing documented by Expo: SDK 57, React Native 0.86, and React 19.2.3.

## Start the backend for a phone

From the project root, start FastAPI so another device on the same Wi-Fi can connect:

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Find the computer’s local IPv4 address, for example `192.168.1.20`. Open the SmartLib app and enter `http://192.168.1.20:8000` as the server address. Android emulators default to `http://10.0.2.2:8000`; iOS simulators default to `http://127.0.0.1:8000`. The address can be changed later from Profile. The local HTTP allowances are for development on a trusted network; use HTTPS when connecting the app to a deployed server.

## Run the native app

Install Node.js 22.13 or newer, then from this folder:

```powershell
npm install
npm.cmd install   
npx.cmd expo start
npx expo start
```

Scan the development QR code with Expo Go, or press `a` to open an Android emulator. For an iOS simulator, use macOS with Xcode and run `npx expo start --ios`.

## Patron features

- Sign in, or create a student or faculty account.
- Confirm the password during sign-up; new accounts must verify their email before sign-in. Sign-in also includes Remember me and Forgot password.
- Sign out after 15 minutes without activity; the API independently expires idle sessions.
- Search the shared catalog and request available books.
- Review borrow request status.
- Display the digital library ID QR code.
- Send a book acquisition suggestion.
- Store the server address securely; save the API token in platform secure storage only when Remember me is selected.

For production Android builds, set `SMARTLIB_ENV=production` in the build environment so the app disallows cleartext HTTP, and point it to the HTTPS deployment. Development builds allow local-network HTTP so a phone can connect to a laptop during setup.
