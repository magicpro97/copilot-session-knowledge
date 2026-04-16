---
title: "App Development Pipeline"
applyTo: "**/app/**,**/src/**,**/ui/**,**/components/**"
---

# App Development Pipeline

## UI/UX Pipeline
```bash
# Design screens (designforge CLI — dùng v0/Figma/Canva, KHÔNG dùng Stitch qua CLI)
designforge generate "onboarding flow" --preset onboarding --device mobile -p v0

# Design screens với Stitch → dùng Stitch MCP (xem stitch-design.instructions.md)

# Extract design system
designforge extract screenshot.png --colors --typography --spacing

# Marketing video
videoforge generate "app promo 30 seconds" --preset product-demo --resolution phone
```

## Store Preparation
```bash
# Automated screenshots
./gradlew :composeApp:jvmTest --tests "*.ScreenshotCaptureTest" --rerun

# Device mockups
screenforge frame screenshot.png --device iphone-17-pro-max --shadow
screenforge composite composite-config.yaml --auto-bg

# Feature graphic (1024×500 for Google Play)
imgforge gen "promotional banner..." --width 1024 --height 500 --output feature_graphic.png --provider replicate

# Deploy
storeforge upload ios ./build/app.ipa
storeforge upload android ./build/app.aab --track beta
```

## App-specific Presets
- **designforge**: `dashboard`, `landing`, `ecommerce`, `saas`, `mobile-app`, `auth`, `onboarding`, `settings`
- **screenforge**: Icons (47 sizes), Splash (13 sizes), Device frames (6 devices)

## Kiến trúc
- **Clean Architecture** hoặc **Feature-first**
- Ưu tiên **KMP/Compose Multiplatform** cho cross-platform
- **expect/actual** pattern cho platform-specific code
- TypeScript/Kotlin strict mode mặc định
- Coverage target ≥ 80%
