# App Development Workflow với Forge Ecosystem

## Quick Start — Tạo app mới

### Mobile App (Expo + Supabase)
```bash
appforge init my-app
# Chọn: Expo → Supabase → Email+Social → NativeWind → Zustand → GitHub Actions → Copilot Agents
```

### Web App (Next.js + Supabase)
```bash
appforge init my-webapp
# Chọn: Next.js → Supabase → Email+Social → Tailwind → Zustand → GitHub Actions → Copilot Agents
```

### Cross-platform (Flutter + Firebase)
```bash
appforge init my-cross-app
# Chọn: Flutter → Firebase → Email+Social → None → Riverpod → GitHub Actions → Copilot Agents
```

---

## Backend Setup

```bash
backforge init supabase
# Hoặc: backforge init firebase | pocketbase | appwrite
```

### E-commerce Schema
```yaml
tables:
  - name: users
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: email, type: string, unique: true }
      - { name: name, type: string }
      - { name: avatar_url, type: string }
      - { name: role, type: string, default: "'user'" }
      - { name: created_at, type: timestamp, default: "now()" }

  - name: products
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: name, type: string }
      - { name: description, type: text }
      - { name: price, type: decimal }
      - { name: image_url, type: string }
      - { name: category, type: string }
      - { name: stock, type: integer, default: "0" }
      - { name: is_active, type: boolean, default: "true" }
      - { name: created_at, type: timestamp, default: "now()" }

  - name: orders
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: user_id, type: uuid, references: users.id }
      - { name: status, type: string, default: "'pending'" }
      - { name: total, type: decimal }
      - { name: shipping_address, type: json }
      - { name: created_at, type: timestamp, default: "now()" }

  - name: order_items
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: order_id, type: uuid, references: orders.id }
      - { name: product_id, type: uuid, references: products.id }
      - { name: quantity, type: integer }
      - { name: price, type: decimal }

  - name: reviews
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: user_id, type: uuid, references: users.id }
      - { name: product_id, type: uuid, references: products.id }
      - { name: rating, type: integer }
      - { name: comment, type: text }
      - { name: created_at, type: timestamp, default: "now()" }
```

### SaaS Schema
```yaml
tables:
  - name: organizations
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: name, type: string }
      - { name: slug, type: string, unique: true }
      - { name: plan, type: string, default: "'free'" }
      - { name: created_at, type: timestamp, default: "now()" }

  - name: users
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: org_id, type: uuid, references: organizations.id }
      - { name: email, type: string, unique: true }
      - { name: name, type: string }
      - { name: role, type: string, default: "'member'" }
      - { name: created_at, type: timestamp, default: "now()" }

  - name: projects
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: org_id, type: uuid, references: organizations.id }
      - { name: name, type: string }
      - { name: description, type: text }
      - { name: status, type: string, default: "'active'" }
      - { name: created_at, type: timestamp, default: "now()" }

  - name: tasks
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: project_id, type: uuid, references: projects.id }
      - { name: assigned_to, type: uuid, references: users.id }
      - { name: title, type: string }
      - { name: description, type: text }
      - { name: status, type: string, default: "'todo'" }
      - { name: priority, type: string, default: "'medium'" }
      - { name: due_date, type: date }
      - { name: created_at, type: timestamp, default: "now()" }
```

```bash
backforge schema apply
backforge types generate    # → src/types/database.ts
backforge seed --count 30
```

---

## UI Design Pipeline

### Mobile App Screens
```bash
# Onboarding
designforge generate "mobile onboarding flow 3 screens fitness app dark theme" \
  --preset onboarding --device mobile

# Auth screens
designforge generate "login screen with email and social buttons minimal" \
  --preset auth --device mobile

# Dashboard
designforge generate "fitness dashboard with activity rings charts progress" \
  --preset dashboard --device mobile --dark

# Settings
designforge generate "settings page with profile toggle switches dark mode" \
  --preset settings --device mobile

# E-commerce
designforge generate "product listing grid with filters search bar" \
  --preset ecommerce --device mobile
```

### Web App Screens
```bash
# Landing page
designforge generate "SaaS landing page hero section pricing features" \
  --preset landing --device desktop

# Dashboard
designforge generate "analytics dashboard with sidebar charts tables" \
  --preset dashboard --device desktop

# Admin panel
designforge generate "admin panel user management data table filters" \
  --preset saas --device desktop
```

### Extract Design Tokens
```bash
designforge extract ./design-reference.png --colors --typography --spacing
# Output: design tokens JSON với color palette, font sizes, spacing scale
```

---

## App Asset Generation

### Product Images
```bash
imgforge generate "professional product photo on white background" \
  --preset photorealistic --ratio square

imgforge generate "app banner gradient abstract modern" \
  --preset minimalist --ratio "16:9"

imgforge generate "user avatar placeholder illustration" \
  --preset flat-design --ratio square --count 5
```

### App Promo Video
```bash
# App showcase
videoforge generate "modern fitness app showcase smooth transitions dark UI" \
  --preset product-demo --resolution phone --duration 15

# Feature highlight
videoforge generate "task management app feature walkthrough" \
  --preset explainer --resolution youtube --duration 10

# Social media teaser
videoforge generate "exciting new app launch countdown reveal" \
  --preset social-media --resolution tiktok --duration 5
```

---

## Testing Pipeline

```bash
# 1. Scan code chưa được test
testforge scan ./src --language ts

# 2. AI gợi ý ưu tiên viết test
testforge suggest ./src --limit 10

# 3. Tạo unit tests
testforge gen src/services/auth.ts --framework vitest
testforge gen src/services/payment.ts --framework vitest
testforge gen src/utils/validators.ts --framework vitest

# 4. Tạo E2E tests
testforge gen:e2e "user signs up, verifies email, completes onboarding" --framework playwright
testforge gen:e2e "user adds product to cart, enters shipping info, completes payment" --framework playwright
testforge gen:e2e "admin creates product, updates price, views analytics" --framework playwright

# 5. Check coverage
testforge coverage . --min 80
```

---

## Store Deployment Pipeline

### Chuẩn bị Assets
```bash
# Icons (từ 1 file nguồn 1024x1024)
screenforge icon app-icon.png --platform all --output ./store-assets/icons

# Splash screens
screenforge splash app-logo.png --platform all --background "#667eea" --output ./store-assets/splashes

# Device mockups cho screenshots
screenforge frame screenshot-1.png --device iphone-15-pro --output ./store-assets/frames
screenforge frame screenshot-2.png --device pixel-8 --output ./store-assets/frames
screenforge frame screenshot-3.png --device ipad-pro --output ./store-assets/frames

# Marketing text
screenforge text framed-shot.png --text "Track Your Fitness Goals" --position top --color "#ffffff" --font-size 48

# ASO metadata
screenforge meta generate -n "FitTrack Pro" -d "AI-powered fitness tracking app" -c health-fitness
screenforge meta translate -i metadata.json -l es   # Spanish
screenforge meta translate -i metadata.json -l ja   # Japanese
screenforge meta translate -i metadata.json -l de   # German
```

### Deploy
```bash
# Setup credentials (one-time)
storeforge init

# Upload builds
storeforge upload ios ./build/FitTrack.ipa
storeforge upload android ./build/FitTrack.aab --track internal

# Sync metadata
storeforge metadata sync metadata.yml --locale en-US

# Check status
storeforge status

# Progressive release
storeforge release android alpha   # internal → alpha
storeforge release android beta    # alpha → beta
storeforge release android         # beta → production
storeforge release ios             # Submit for App Review
```

---

## Monitoring Production

```bash
# Setup
monforge config set sentry.authToken "your-token"
monforge config set sentry.orgSlug "your-org"
monforge config set sentry.projectSlug "fittrack"
monforge config set appstore.appId "123456789"

# Alerts
monforge alerts set "crash-rate > 0.5"
monforge alerts set "rating < 4.2"
monforge alerts set "error-count > 50"
monforge alerts set "anr-rate > 0.2"

# Daily routine
monforge status                    # Quick health check
monforge errors --limit 5          # Top crashes
monforge reviews ios               # User feedback
monforge report --format md        # Weekly report
```

---

## CI/CD Template (GitHub Actions)

```yaml
name: Build & Deploy
on:
  push:
    tags: ['v*']

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm ci
      - run: testforge coverage . --min 80 --json

  deploy-android:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: flutter build appbundle
      - run: |
          storeforge config set google.serviceAccountPath ${{ secrets.PLAY_SA_PATH }}
          storeforge config set google.packageName com.example.fittrack
          storeforge upload android build/app.aab --track beta
          storeforge metadata sync metadata.yml

  deploy-ios:
    needs: test
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - run: flutter build ipa
      - run: |
          storeforge config set apple.issuerId ${{ secrets.APPLE_ISSUER }}
          storeforge config set apple.keyId ${{ secrets.APPLE_KEY_ID }}
          storeforge config set apple.privateKeyPath ${{ secrets.APPLE_P8_PATH }}
          storeforge upload ios build/Runner.ipa
          storeforge release ios

  monitor:
    needs: [deploy-android, deploy-ios]
    runs-on: ubuntu-latest
    steps:
      - run: |
          monforge config set sentry.authToken ${{ secrets.SENTRY_TOKEN }}
          monforge alerts set "crash-rate > 1"
          monforge status
```
