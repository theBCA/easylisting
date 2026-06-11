# Design Review

Review UI/UX against the EasyListing design system.

## Design system tokens (Theme.swift)

**Colors:** `Theme.purple` (#5B47E0), `Theme.purpleLight` (#F0EEFF), `Theme.green` (#059669), `Theme.amber` (#F59E0B)
**Semantic:** `Theme.bg`, `Theme.card`, `Theme.border`, `Theme.textPrimary/Secondary/Tertiary`
**Gradients:** `Theme.purpleGradient` (button fill), `Theme.heroGradient` (auth screen background)
**Spacing:** `Theme.Space.xs/sm/md/lg/xl` = 4/8/16/24/32
**Radius:** `Theme.radius` = 16, `Theme.radiusSmall` = 10, `Theme.radiusLarge` = 24
**Shadows:** `Theme.cardShadow` / `Theme.buttonShadow` — always use these, never raw `.shadow(color: .black)`

## Components (Components.swift)

- `PrimaryButton` — 52px height, purple gradient, white text, shadow
- `SecondaryButton` — 52px height, purpleLight bg, purple border
- `SectionCard(title:)` — white card with `Theme.radius` corners + subtle shadow
- `InputField(label:)` — labeled text field
- `TagPill` — purple capsule chip
- `PremiumBadge` — amber crown label
- `RemainingPill(count:)` — red when 0, purple otherwise
- `ErrorBanner` / `SuccessBanner` — full-width banners with icon

## Checklist

1. All spacing uses `Theme.Space.*` constants — no magic numbers
2. Text colours use `Theme.textPrimary/Secondary/Tertiary` or `.white` — never `.black` or `.gray`
3. Backgrounds use `Theme.bg` (screen) or `Theme.card` (card) — no `.white` direct
4. Buttons are `PrimaryButton` or `SecondaryButton` — no raw `Button { }.background(Color.purple)`
5. Cards use `SectionCard` or the standard `.background(Theme.card).clipShape(...).shadow(color: Theme.cardShadow, ...)` pattern
6. Interactive elements ≥ 44pt tap target
7. Loading states use `isLoading: true` on `PrimaryButton` — no ad-hoc `ProgressView` in button labels
8. Dark mode: adaptive colours (`Theme.bg`, `Theme.card`, `Theme.border`) used — no hardcoded light-mode values

## Usage

Run: `/design-review [SwiftUI file or screen name]`

$SHELL: find /Users/berk.arslan/Desktop/etsy/ios/EasyListing -name "*.swift" | xargs grep -l "Color\.\(black\|white\|gray\)" 2>/dev/null
