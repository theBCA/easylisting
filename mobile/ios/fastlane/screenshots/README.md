# App Store Screenshots

Screenshots go in `en-US/` with this naming convention:
  `<device-prefix>-<order>-<screen>.png`

## Required device sizes (App Store Connect)

| Folder / prefix    | Simulator to use            | Resolution      |
|--------------------|-----------------------------|-----------------|
| `iPhone 6.7"`      | iPhone 16 Pro Max           | 1320 × 2868 px  |
| `iPhone 6.5"`      | iPhone 14 Plus or 11 Pro Max| 1242 × 2688 px  |

Apple only requires one set (6.5" or 6.7"). With just 6.7" you're covered.

## How to take them

1. Open Xcode → Simulator → choose **iPhone 16 Pro Max**
2. Run the EasyListing scheme (▶)
3. Navigate to each screen below
4. Press **⌘ + S** (or Device > Screenshot) — file saves to Desktop
5. Rename and move to `fastlane/screenshots/en-US/`

## Recommended screens (5 max, 3 minimum)

| Filename                                  | What to show                                  |
|-------------------------------------------|-----------------------------------------------|
| `iPhone 6.7-01-generate.png`             | Generate screen with a product photo loaded   |
| `iPhone 6.7-02-result.png`               | Result screen showing generated title + tags  |
| `iPhone 6.7-03-publish.png`              | Result screen scrolled to the publish button  |
| `iPhone 6.7-04-listings.png`             | Listings tab showing saved items              |
| `iPhone 6.7-05-settings.png`             | Settings / Etsy connected screen              |

## After adding screenshots

Run from `mobile/ios/`:
```
fastlane store_assets
```
