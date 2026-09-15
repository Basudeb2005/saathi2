# The phone app

A WebView around the console the Pi already serves, plus the one thing a
web page cannot do: **ask over Bluetooth where the Pi is.**

That is the whole reason it exists. To open the web console you need the
Pi's address; to learn the address you need to reach it; to reach it you
need the network it may not be on. Bluetooth is a second radio that
doesn't care about any of that — pair once, tap **Find over Bluetooth**,
and the app gets the address and the key together and loads the console.

Everything after that is the page the Pi serves, which means improving
the console never means shipping a new APK.

## Getting the APK

It is built by CI, not committed — a binary in a repo is a binary that
stops matching the source.

1. **Actions** → **Build phone app** → **Run workflow** (or push a change
   under `android/`)
2. Open the finished run → **Artifacts** → `saathi-console-apk`
3. Unzip, put `app-debug.apk` on your phone, tap it
4. Android will ask whether to allow installs from wherever you opened it
   from — allow it for that app

It is a debug build, unsigned by any release key. That is deliberate: a
release keystore committed to a public repo is not a keystore.

## Building it yourself

```bash
cd android
gradle assembleDebug          # or ./gradlew if you make a wrapper
# app/build/outputs/apk/debug/app-debug.apk
```

Needs JDK 17 and an Android SDK with platform 34. Android Studio has
both — **Open** this `android/` folder and press Run.

## If it can't find the Pi

The app only looks at devices your phone has **already paired with**, on
purpose — a scan finds everything in the building and then asks you to
pick out the thing you can't identify. So pair first, in Android's own
Bluetooth settings; the Pi appears under its hostname.

Then, in order:

- Is the Pi powered on, and did it finish booting (~40s)?
- `systemctl status saathi-console@<user>` — is the console running?
- Does a free serial terminal app (Kai Morich's "Serial Bluetooth
  Terminal") see it and answer `status`? If that works and this doesn't,
  it's the app; if neither works, it's the Pi.

## What it does not do

No scanning, no pairing, no notifications, no background service. It is a
finder and a WebView. Anything cleverer belongs in the page, where it
ships the moment the Pi is updated.
