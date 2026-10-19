# Built-in AI APIs for Chromium on iOS

Chromium for iOS (the WebKit-based build) with the
[built-in AI APIs](https://developer.chrome.com/docs/ai/built-in-apis):
`LanguageModel` (the Prompt API), `Summarizer`, `Writer`, `Rewriter`,
`Translator`, and `LanguageDetector`. Every page gets them, and inference
happens on the device:

- **Apple Foundation Models** where the device has Apple Intelligence. Text
  input works from iOS 26, and image input from iOS 27.
- **Gemma 4 E2B** through
  [LiteRT-LM](https://github.com/google-ai-edge/LiteRT-LM) everywhere else, and
  for audio input. The model is downloaded once into the app's container and
  shared by all origins. It runs on the GPU on devices with 6 GB of RAM or more,
  and on the CPU on 4 GB devices.

## Architecture

Chromium injects the
[Prompt API polyfill](https://www.npmjs.com/package/prompt-api-polyfill) and the
[task API polyfills](https://www.npmjs.com/package/built-in-ai-task-apis-polyfills)
into every frame at document start, in the page's own JavaScript world. The
polyfill's `native` backend sends each call over a WebKit script message handler
to `BuiltInAIEngine`, a Swift component of the browser that picks the model,
keeps the sessions, and streams the answers back.

```
page ── LanguageModel / Summarizer / … (polyfills, page world)
          │  native backend
          ▼
       __builtInAINativeBridge (gCrWeb, script message handler)
          │
          ▼
       BuiltInAIEngine (Swift) ── Apple Foundation Models
                               └─ Gemma 4 E2B via LiteRT-LM
```

## Repository layout

- `patches/`: the Chromium change, as `git format-patch` output. It applies to
  Chromium `main` as of October 2026.
- `bundle/`: builds the polyfill script that Chromium injects, and a test page
  for the APIs.
- `ios-tools/`: provisions a device build through the App Store Connect API,
  with no Xcode UI involved.

## Building

1. Get a Chromium checkout with `target_os = ["ios"]` in `.gclient`, and apply
   the patch:

   ```sh
   cd chromium/src
   git am /path/to/chromium-ios-built-in-ai/patches/*.patch
   python3 third_party/litert_lm/fetch.py
   ```

2. Configure a build. For the simulator:

   ```gn
   target_os = "ios"
   target_environment = "simulator"
   target_cpu = "arm64"
   use_blink = false
   is_debug = false
   enable_litert_lm = true
   ```

   For a device, use `target_environment = "device"` and add the GN args from
   `ios-tools/signing.json` (see below).

3. Build with `autoninja -C out/<dir> chrome`.

The APIs are behind the `ios-built-in-ai-apis` flag in `chrome://flags`, which
is on by default in this patch.

## Testing in the simulator

`ios-tools/run-simulator.sh` installs a simulator build, opens
`bundle/test/index.html` in it, and prints what the page reports:

```sh
ios-tools/run-simulator.sh /path/to/chromium/src/out/ios-sim/Chromium.app
```

It needs [AXe](https://github.com/cameroncooke/AXe) to accept the "Open in
Chromium?" prompt. With Apple Foundation Models on an iOS 27 simulator, all
checks pass: `availability()`, `create()`, `prompt()`, `promptStreaming()`,
`responseConstraint`, `AbortSignal`, `clone()`, `measureContextUsage()`,
`Summarizer`, and `Translator`.

## Signing for a device

`ios-tools/asc_provision.py` takes an
[App Store Connect API key](https://developer.apple.com/documentation/appstoreconnectapi/creating-api-keys-for-app-store-connect-api)
and creates the certificate, device registration, App ID, and development
profile a device build needs:

```sh
ios-tools/asc_provision.py --key-id <key id> --issuer-id <issuer id> \
    --key ~/.appstoreconnect/private_keys/AuthKey_<key id>.p8 \
    --udid <device udid> --prefix com.example.chromium
```

It keeps the signing identity in a keychain of its own, so `codesign` runs
without prompts, and writes the GN args to `ios-tools/signing.json`. The build
then signs with a minimal set of entitlements
(`ios_chrome_minimal_entitlements`), since Chromium's default ones need app
groups that the API cannot create.

## Updating the polyfills

`bundle/update-chromium.sh` builds the polyfills from a
[web-ai-demos](https://github.com/GoogleChromeLabs/web-ai-demos) checkout and
writes the result into Chromium:

```sh
cd bundle && npm install
WEB_AI_DEMOS=/path/to/web-ai-demos ./update-chromium.sh /path/to/chromium/src
```

The `native` backend it needs lives on the `built-in-ai-ios` branch of
[tomayac/web-ai-demos](https://github.com/tomayac/web-ai-demos/tree/built-in-ai-ios)
until it lands upstream.

## License

Apache 2.0. The files in `patches/` are changes to Chromium and carry Chromium's
BSD-style license headers.
