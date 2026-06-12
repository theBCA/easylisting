fastlane documentation
----

# Installation

Make sure you have the latest version of the Xcode command line tools installed:

```sh
xcode-select --install
```

For _fastlane_ installation instructions, see [Installing _fastlane_](https://docs.fastlane.tools/#installing-fastlane)

# Available Actions

## iOS

### ios test

```sh
[bundle exec] fastlane ios test
```

Run tests

### ios certs

```sh
[bundle exec] fastlane ios certs
```

Fetch/create signing certs + profiles via App Store Connect API (no match repo needed)

### ios beta

```sh
[bundle exec] fastlane ios beta
```

Build + upload to TestFlight

### ios release

```sh
[bundle exec] fastlane ios release
```

Build + upload to App Store (metadata + binary, no auto-submit)

### ios upload_screenshots

```sh
[bundle exec] fastlane ios upload_screenshots
```

Upload App Store screenshots only (no binary, no metadata)

### ios upload_metadata

```sh
[bundle exec] fastlane ios upload_metadata
```

Upload App Store metadata only (no binary, no screenshots)

### ios store_assets

```sh
[bundle exec] fastlane ios store_assets
```

Upload App Store metadata + screenshots only (no binary, no auto-submit)

### ios screenshots

```sh
[bundle exec] fastlane ios screenshots
```

Capture App Store screenshots via UITests (fastlane snapshot)

### ios build_local

```sh
[bundle exec] fastlane ios build_local
```

Build locally for simulator (no signing)

----

This README.md is auto-generated and will be re-generated every time [_fastlane_](https://fastlane.tools) is run.

More information about _fastlane_ can be found on [fastlane.tools](https://fastlane.tools).

The documentation of _fastlane_ can be found on [docs.fastlane.tools](https://docs.fastlane.tools).
