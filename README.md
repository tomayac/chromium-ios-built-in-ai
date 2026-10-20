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
  for audio input. Each model file is downloaded once into the app's container
  and shared by all origins, and downloads resume where they stopped.

## Installing

Install the app on an iPhone through [AltStore](https://altstore.io) or
[SideStore](https://sidestore.io), with any Apple ID, free ones included: open
the [install page](https://tomayac.github.io/chromium-ios-built-in-ai/altstore/)
on the iPhone and add the source, or add
`https://tomayac.github.io/chromium-ios-built-in-ai/altstore/source.json` by
hand. The [test pages](https://tomayac.github.io/chromium-ios-built-in-ai/)
exercise the APIs.

## Gemma files and backends

The session's `expectedInputs` decide which Gemma file it needs, and the GPU
family decides where that file runs correctly:

| Session       | GPU family 6 (A13)                  | GPU family 7 and later (A14, M1 on)                           |
| ------------- | ----------------------------------- | ------------------------------------------------------------- |
| Text only     | `gemma-4-E2B-it-gpu.litertlm` (GPU) | the same, or the multimodal file if it is already there (GPU) |
| Images, audio | `gemma-4-E2B-it.litertlm` (CPU)     | `gemma-4-E2B-it.litertlm` (GPU)                               |

The text file (2.0 GB) runs on the GPU only. The multimodal file (2.6 GB)
produces random tokens on the A13 GPU, at 16 and 32 bits alike, and runs with
32-bit activations on family 7, where 16 bits corrupt digits
([LiteRT-LM #2814](https://github.com/google-ai-edge/LiteRT-LM/issues/2814),
[#3913](https://github.com/google-ai-edge/LiteRT-LM/issues/3913)). GPUs of
family 5 and older, untested, use the multimodal file on the CPU. The GPU is
only used when the app has the memory for it.

## Model memory

Apple Foundation Models run in a system process that loads and evicts the model
on its own. Gemma runs in the browser, which manages it:

- An engine loads at a session's first prompt, not at `create()`, and Gemma runs
  one turn at a time across all tabs.
- Each session keeps a conversation with its engine, holding the KV cache, so
  the next turn does not re-read the history.
- A second engine, for a session needing another file, stays loaded only while
  the app has room for it. Otherwise the other engine goes first.
- Engines are evicted on memory warnings, a minute after the last session is
  gone, and 20 seconds after the browser goes to the background, within
  background task time, before iOS suspends it. Evicted sessions keep working:
  their next turn reloads the engine and rebuilds the conversation from the
  history the polyfill sends with every turn.
- GPU turns wait for the foreground, since iOS does not let background apps use
  the GPU. A GPU turn running as the browser goes to the background stops, and
  goes on in the foreground where it stopped, so the page gets one answer. The
  browser renders the conversation in Gemma's prompt format with the model's
  turn left open after the partial answer, and decodes from there in a LiteRT-LM
  session without a template of its own. Conversations with images or audio, and
  turns with a response schema, run again from scratch, as long as the page has
  none of their answer yet.

`bundle/test/multimodal.html` checks image and audio input with answers that are
easy to verify: a canvas and a PNG `Blob` as images, a WAV `Blob` and an
`AudioBuffer` of speech as audio, and both in one prompt.

`bundle/test/digits.html` asks for strings to be repeated verbatim (dates, phone
numbers, long digit runs, codes, text with typos), where GPU precision problems
show first. On an iPhone 11 Pro Max, the text-only GPU file repeats all of them
exactly at 16 and at 32 bits; 32 bits costs 12% of decode speed and twice the
load time, so 16 bits stays the default there.

`bundle/test/compacting/` is the session compacting demo of web-ai-demos with an
autopilot (`?auto`): it chats in English, German, and French, compacts the
session with Language Detector and Summarizer, and asks one more question. It
passes on the simulator (Apple Foundation Models) and on the iPhone (Gemma).

Test pages opened with `?v=<seconds since the epoch>` do not run again when the
browser restores them as tabs on a later launch.

`bundle/test/lifecycle.html` exercises this: a text session, an image session,
which needs the other file, and a second text turn.

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
  with no Xcode UI involved, and releases builds for AltStore and TestFlight.
- `altstore/`: the AltStore source and its install page.
- `icon/`: the app icon, as SVG and as the 1024 by 1024 PNG the build uses.
- `site/`: other pages for GitHub Pages, such as the privacy policy.

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

## Benchmarking on a device

`ios-tools/bench-device.sh` relaunches Chromium on a device once per
configuration, opens `bundle/test/bench.html`, and prints the stats the browser
records after the answer:

```sh
ios-tools/bench-device.sh <udid> <bundle id> auto gpu cpu cpu:2
```

It passes developer launch arguments that the browser reads:
`-BuiltInAIBackend apple|gemma|auto`, `-BuiltInAIGemmaBackend cpu|gpu|auto`,
`-BuiltInAIGemmaModelFile <name>`, `-BuiltInAIGemmaThreads <n>`,
`-BuiltInAIGemmaActivation f16|f32`, and `-BuiltInAIKeepAwake YES`, which keeps
the device from locking while Chromium is in front. `BENCH_EXTRA_ARGS` adds more
of them, and `BENCH_URL` points at another page, such as `bench.html?image` for
a session with image input. The stats, including the start of each answer, go to
`Library/Caches/BuiltInAI/stats.jsonl` in the app's container.

Gemma 4 E2B on an iPhone 11 Pro Max (A13, 4 GB, no Apple Intelligence):

| File and backend         | Decode             | First token | Peak footprint | Answer        |
| ------------------------ | ------------------ | ----------- | -------------- | ------------- |
| Text file, GPU           | 21–22 tokens/s     | 2.2–2.6 s   | 0.43 GB        | correct       |
| Multimodal, CPU          | 13.6–13.9 tokens/s | 2.5 s       | 0.63 GB        | correct       |
| Multimodal, GPU          | 20.5 tokens/s      | –           | 1.19 GB        | random tokens |
| Multimodal, GPU, 32 bits | 17.4 tokens/s      | –           | 1.37 GB        | random tokens |

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

Two capabilities of the App ID need one visit to the
[developer portal](https://developer.apple.com/account/resources/identifiers/list),
since the API cannot enable them: **Extended Virtual Addressing**, without which
Chromium's allocator cannot start, and **Increased Memory Limit**, which gives
Gemma room on devices with 4 GB of RAM. Run the script again after enabling
them, so the profile includes them.

## Releasing for AltStore and SideStore

AltStore and SideStore re-sign apps with the user's Apple ID, which only gets
the capabilities a free account can have. The `ios_chrome_sideload_entitlements`
GN arg signs the build with just one of them, **Increased Memory Limit**.
Without extended virtual addressing, PartitionAlloc shrinks its pools until they
fit, and Gemma still runs on the GPU and the CPU.

```gn
target_environment = "device"
is_debug = false
dcheck_always_on = false
symbol_level = 0
enable_litert_lm = true
ios_enable_memory_debugging = false
ios_chrome_sideload_entitlements = true
ios_chrome_display_name = "Built-In AI"
ios_application_icons_target = "//ios/chrome/app/resources:built_in_ai_icons"
ios_application_icons_target_legacy = "//ios/chrome/app/resources:no_icons"
```

The last three lines name the app "Built-In AI" on the Home Screen and give it
the icon from `icon/icon.svg`, free of Chromium's branding.

`ios_enable_memory_debugging = false` leaves out the memory debugger, whose
private UIKit call App Store Connect rejects.

Build it with the signing GN args from above, then package it:

```sh
ios-tools/make-altstore-release.sh out/ios-sideload/Chromium.app altstore \
    "What changed in this release."
gh release create v<build> altstore/Chromium-<build>.ipa
```

The script signs the app ad hoc, keeping the sideload entitlements and dropping
the development certificate and the provisioning profile, which lists device
IDs. It writes the `.ipa` and adds the release to `altstore/source.json`, with
the entitlements and privacy descriptions AltStore shows before installing.
Publishing the `altstore/` folder to GitHub Pages updates the source.

Both release scripts run `ios-tools/rebrand_strings.py`, which puts the app's
display name in place of "Chromium" in the permission texts of every language,
such as the camera and location prompts.

## Releasing on TestFlight

`ios-tools/testflight.py` takes the same build and ships it through TestFlight:

```sh
ios-tools/testflight.py --key-id <key id> --issuer-id <issuer id> \
    --app out/ios-sideload/Chromium.app --bundle-id com.example.browser \
    --notes "What to test in this build." --release
```

It creates an Apple Distribution certificate and an App Store profile, and
re-signs a copy of the app with them, the next free build number,
`PrivacyInfo.xcprivacy`, and `ITSAppUsesNonExemptEncryption` set to `NO`, since
Chromium only uses exempt, standard encryption. TestFlight builds get both
memory capabilities, extended virtual addressing included. It then validates and
uploads the `.ipa` with `altool`, and waits until App Store Connect has
processed it. With `--release`, it fills in the beta app information and the
Beta App Review contact from `ios-tools/testflight.json` (see
`testflight.example.json`; it stays out of git), adds the build to a group with
a public link, and submits it for review.

`--notarize` submits the build for notarization, which alternative app
marketplaces such as AltStore PAL need. It goes out as the app version of the
build's `CFBundleShortVersionString`, with review type Notarization. The app has
to be available in the EU, and App Store Connect needs screenshots for it, which
`ios-tools/upload_screenshots.py` uploads.

The API cannot create apps, so the app record needs one visit to
[App Store Connect](https://appstoreconnect.apple.com/apps) first: **New App**,
iOS, any unique name, and the App ID's bundle ID.

## Releasing on AltStore PAL

AltStore PAL, the AltStore edition from the EU app marketplace, installs only
apps that Apple has notarized, and needs no Apple ID or computer. After
`testflight.py --notarize` and Apple's review:

```sh
ios-tools/altstore_pal.py --key-id <key id> --issuer-id <issuer id> \
    --app <app Apple ID> --version <notarized version>
```

The script finds the alternative distribution package Apple generated for the
version, has AltStore process it, and unpacks it into `altstore/pal/<build>/`.
It writes `altstore/pal/source.json`, a source of its own with the app's
`marketplaceID`, since AltStore PAL and AltStore Classic read different kinds of
downloads. Once `altstore/pal/` is on GitHub Pages, the install page shows an
**Add to AltStore PAL** button, which opens AltStore PAL through its own
`altstore-pal://` scheme even where AltStore Classic is installed too.

The app first has to be authorized for AltStore PAL once: AltStore issues a
marketplace token for the team's Developer ID
(`POST https://api.altstore.io/register`), which goes into App Store Connect
under **Users and Access → Integrations → Marketplace**.

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
