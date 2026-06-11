# iOS Release

Upload a new TestFlight build.

## What this does

1. Increments the build number with `agvtool`
2. Archives the app with `xcodebuild` (Release scheme, no signing)
3. Uploads to TestFlight via `fastlane beta`

## Prerequisites

- `ios/fastlane/.env` must contain `APP_STORE_CONNECT_API_KEY_KEY_ID`, `APP_STORE_CONNECT_API_KEY_ISSUER_ID`, `APP_STORE_CONNECT_API_KEY_KEY` (base64 `.p8`)
- Xcode project regenerated if `project.yml` changed: `cd ios && xcodegen generate`
- App ID: **6778954593**, Bundle ID: `com.thebca.easylisting`

## Run

$SHELL: cd /Users/berk.arslan/Desktop/etsy/ios && bundle exec fastlane beta
