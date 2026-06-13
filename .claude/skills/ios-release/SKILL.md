---
name: ios-release
description: Upload a new TestFlight build of the EasyListing iOS app via fastlane. Ships a build to App Store Connect — only run when the user explicitly asks for a release.
disable-model-invocation: true
---

## iOS Release — TestFlight upload

### What this does

1. Increments the build number with `agvtool`
2. Archives the app with `xcodebuild` (Release scheme, no signing)
3. Uploads to TestFlight via `fastlane beta`

### Prerequisites

- `mobile/ios/fastlane/.env` must contain `APP_STORE_CONNECT_API_KEY_KEY_ID`, `APP_STORE_CONNECT_API_KEY_ISSUER_ID`, `APP_STORE_CONNECT_API_KEY_KEY` (base64 `.p8`)
- If `project.yml` changed, regenerate the Xcode project first: `cd mobile/ios && xcodegen generate`
- App ID: **6778954593**, Bundle ID: `com.thebca.easylisting`

### Run

From the repo root, run:

```
cd mobile/ios && bundle exec fastlane beta
```

Confirm the build number bumped and the upload succeeded before reporting done.
