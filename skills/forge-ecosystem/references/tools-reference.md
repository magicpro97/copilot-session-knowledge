# Forge Ecosystem — Per-Tool Reference

Detailed command reference for all 10 CLI tools. For workflow guidance, see `SKILL.md`.

---

## 1. AppForge — Universal App Scaffolding

**Command:** `appforge`
**Config:** `~/.appforge/config.json`

### Commands
```bash
appforge init [name]              # Wizard tạo dự án mới (10 bước)
appforge add <module>             # Thêm module: auth, analytics, push, payments
appforge template list|save       # Quản lý template
appforge config set|list          # Cấu hình defaults
```

### Wizard 10 bước
1. Project name → 2. Framework (Expo/Flutter/Next.js/Vite/Astro) → 3. Backend (Supabase/Firebase/PocketBase/Appwrite/None) → 4. Auth (Email/Social/Magic Link) → 5. Styling (Tailwind/NativeWind/styled-components/CSS Modules) → 6. State Management (Zustand/Redux/Riverpod/Provider) → 7. CI/CD (GitHub Actions) → 8. Copilot Agents → 9. README → 10. Landing Page

### Built-in Templates
- `expo-supabase` — Expo + Supabase + NativeWind + Zustand
- `flutter-firebase` — Flutter + Firebase + Riverpod
- `nextjs-supabase` — Next.js + Supabase + Tailwind + Zustand

### Kiến trúc
```
src/
├── cli/commands/    # init, add, template, config
├── core/            # scaffolder, config, readme, landing, templates
├── generators/      # expo, flutter, nextjs, vite, astro
├── modules/         # auth, backend, styling, cicd, copilot
└── types/
```

Pipeline: Framework CLI (`create-expo-app`, `flutter create`, `create-next-app`) → Backend client → Auth → Styling → CI/CD → Copilot agents → README/Landing

---

## 2. BackForge — Backend Bootstrapper

**Command:** `backforge`
**Config:** `~/.backforge/config.json` (global) + `./backforge.json` (local)

### Commands
```bash
backforge init [provider]         # Khởi tạo backend (Supabase/Firebase/PocketBase/Appwrite)
backforge schema define           # Interactive wizard tạo tables/columns
backforge schema apply            # Apply schema → migrations
backforge schema export           # Export YAML
backforge types generate          # Tạo TypeScript interfaces (Base, Insert, Update)
backforge seed [--count N]        # Fake data thông minh (context-aware)
backforge deploy                  # Deploy lên cloud
backforge status                  # Health check
backforge config set|list         # Cấu hình credentials
```

### Schema Format (YAML)
```yaml
tables:
  - name: users
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: email, type: string, unique: true }
      - { name: name, type: string }
      - { name: created_at, type: timestamp, default: "now()" }
  - name: posts
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: title, type: string }
      - { name: author_id, type: uuid, references: users.id }
```

### Column Types
`uuid`, `string`, `text`, `integer`, `float`, `decimal`, `boolean`, `timestamp`, `date`, `json`

### Type Generation
Each table → 3 interfaces: `User`, `UserInsert` (no PK & defaults), `UserUpdate` (all optional)

### Provider Differences
| Provider | Schema Output | Seed Output | Deploy Command |
|----------|--------------|-------------|----------------|
| Supabase | SQL migrations | SQL INSERT | `supabase db push` |
| Firebase | Firestore rules | Firestore docs | `firebase deploy` |
| PocketBase | Collection JSON | Record counts | PocketBase admin |
| Appwrite | Collection JSON | Document counts | Appwrite CLI |

---

## 3. DesignForge — AI Design Generator

**Command:** `designforge`
**Config:** `~/.designforge/`

### Commands
```bash
designforge generate <prompt>     # Tạo UI design từ text (alias: gen, g)
designforge edit <prompt>         # Sửa design có sẵn (-i input)
designforge vary <design-id>      # Tạo biến thể
designforge extract <file>        # Extract design tokens (colors, typography, spacing)
designforge batch <config.yaml>   # Batch processing
designforge compare <prompt>      # So sánh giữa providers
designforge template save|list|show|delete
designforge cost summary|pricing
designforge config set|list
```

### 4 Providers
| Provider | Output | Cost | Key | Status |
|----------|--------|------|-----|--------|
| **Google Stitch** | HTML, PNG | FREE | Required (Labs key) | ⚠️ CLI 404 — use SDK |
| **Vercel v0** | React/JSX | $0.05/design | Required | ✅ Working |
| **Figma API** | PNG, SVG, PDF | FREE | Required | ✅ Working |
| **Canva Connect** | PNG, PDF | $0.01/design | Required | ✅ Working |

### 12 Style Presets
`dashboard`, `landing`, `ecommerce`, `saas`, `mobile-app`, `blog`, `portfolio`, `social-media`, `email`, `onboarding`, `settings`, `auth`

### Device Types
- `mobile` (375×812), `desktop` (1440×900), `tablet` (768×1024), `agnostic`

### ⚠️ Google Stitch — Important
- **designforge CLI Stitch provider calls REST API → always 404** (Google hasn't public-ized the REST endpoint)
- **Use Stitch MCP Server or `@google/stitch-sdk` directly**
- Env var: `STITCH_API_KEY` (Google Labs API key format `AQ.Ab8...`)
- **SDK wrapper BUG**: `project.generate()` crashes because `outputComponents[0]` is `designSystem`, NOT `design.screens`
- Each generation takes ~30-60 seconds
- Stitch creates its own design system per screen (no pre-creation needed)
- Prompt should specify hex colors, font sizes, border radius, layout precisely
- Output: HTML (renderable) + PNG screenshot

### Stitch MCP Server (RECOMMENDED for AI agents)

Stitch MCP proxy installed globally at `~/.agents/mcp-servers/stitch-proxy.mjs`.
Configured for: **Cursor**, **IntelliJ Copilot**, **Gemini CLI**.

**If IDE/agent has MCP support → call tools directly (no script needed):**

| MCP Tool | Purpose | Params |
|----------|---------|--------|
| `create_project` | Create project | `title` |
| `list_projects` | List projects | — |
| `generate_screen_from_text` | Generate UI screen | `projectId`, `prompt`, `deviceType` (MOBILE/DESKTOP/TABLET) |
| `edit_screens` | Edit screen | `projectId`, `screenId`, `prompt` |
| `generate_variants` | Create variants | `projectId`, `screenId`, `prompt`, `variantCount` |
| `list_screens` | List screens | `projectId` |
| `get_screen` | Screen details (image/HTML URLs) | `projectId`, `screenId`, `name` |
| `create_design_system` | Create design system | `projectId`, `title`, `content` |
| `apply_design_system` | Apply DS | `projectId`, `designSystemId`, `screenIds` |

**Standard workflow:**
1. `create_project` → get projectId
2. `generate_screen_from_text` (detailed prompt: hex colors, sizes, layout)
3. `list_screens` → get screen list
4. `get_screen` → get image URL + HTML download URL
5. (Optional) `edit_screens` / `generate_variants` to refine

---

## 4. ImgForge — AI Image Generator

**Command:** `imgforge`
**Config:** `~/.imgforge/`

### Commands
```bash
imgforge generate <prompt>        # Generate image from text (alias: gen, g)
imgforge edit <prompt>            # Edit image (-i input, --mask)
imgforge upscale <input>          # Upscale 2x-4x
imgforge remove-bg <input>        # Remove background → transparent PNG
imgforge vary <input>             # Create variants
imgforge batch <file.yaml>        # Batch processing
imgforge compare <prompt>         # Compare providers
imgforge convert <input>          # Convert format (png, jpg, webp)
imgforge interactive              # Interactive menu mode
imgforge template save|list|show|delete
imgforge cost summary|pricing
imgforge providers list
imgforge history list
```

### Generate Options
| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--provider` | `-p` | Provider (openai, gemini, stability, replicate, pollinations) | — |
| `--model` | `-m` | Specific model | — |
| `--width` | `-W` | Width in pixels | 1024 |
| `--height` | `-H` | Height in pixels | 1024 |
| `--ratio` | `-r` | Aspect ratio (9:16, 16:9, 1:1…) | — |
| `--preset` | `-s` | Style preset | — |
| `--output` | `-o` | Output file | auto |
| `--format` | `-f` | Format (png, jpg, webp) | png |
| `--count` | `-n` | Number of images | 1 |
| `--quality` | `-q` | Quality (standard, hd, ultra) | standard |
| `--negative` | — | Negative prompt | — |
| `--seed` | — | Seed for reproducibility | — |
| `--enhance` | — | AI prompt enhancement | false |
| `--open` | — | Open image after generation | false |

### 5 Providers
| Provider | Models | Cost | API Key |
|----------|--------|------|---------|
| **Pollinations** | flux, flux-realism, flux-anime, turbo | **FREE** | Not required |
| **Google Gemini** | Imagen 4/3, gemini-2.5-flash | $0.039-0.060 | Required |
| **OpenAI** | DALL-E 3/2, gpt-image-1 | $0.04-0.12 | Required |
| **Stability AI** | SD3, Stable Image Ultra/Core | $0.035-0.065 | Required |
| **Replicate** | Flux Pro/Schnell, SDXL | $0.003-0.04 | Required |

### 15 Style Presets
`photorealistic`, `anime`, `flat-design`, `watercolor`, `3d-render`, `neon`, `pixel-art`, `oil-painting`, `sketch`, `comic`, `minimalist`, `retro`, `cyberpunk`, `fantasy`, `isometric`

### 17 Aspect Ratios
`16:9`, `9:16`, `1:1`, `4:3`, `3:4`, `phone`, `og-image`, `instagram`, `story`, `portrait`, `landscape`, `twitter`, `facebook`, `linkedin`, `pinterest`, `youtube`, `tiktok`

### Important — Practical Notes
- **Aspect ratio**: Use `-r 9:16` (NOT `--aspect` — will error `unknown option`)
- **Custom size**: Use `-W <width> -H <height>` for specific dimensions
- **Style preset**: Use `-s` (NOT `--preset`)
- **Replicate flux-1.1-pro**: Max height **1440px** — use `-W 816 -H 1440` for mobile portrait
- **Replicate flux-schnell**: Fast (~2s), cheap ($0.003), but lower quality
- **Pollinations FREE but rate limited**: Prone to 429 when running parallel → run sequentially
- **DO NOT run in parallel** — rate limit 6 req/min (Replicate) → add `sleep 12` between requests
- **`--enhance`** uses AI to upgrade prompt (adds quality keywords)
- **`--seed`** for reproducible results
- **Provider priority**: `replicate` with `flux-1.1-pro` for production, `pollinations` for draft

```bash
# ✅ CORRECT — sequential, correct options, correct max size
npx imgforge generate "dark gradient background" -p replicate -m black-forest-labs/flux-1.1-pro -W 816 -H 1440 -o bg.png
sleep 12
npx imgforge generate "character sprite" -p replicate -m black-forest-labs/flux-1.1-pro -r 1:1 -s pixel-art -o char.png

# ❌ WRONG — wrong options, parallel
imgforge generate "bg" --aspect 9:16 --preset pixel-art  # --aspect doesn't exist
imgforge generate "a" -o a.png & imgforge generate "b" -o b.png &  # 429 rate limit
```

---

## 5. AudioForge — AI Audio Generator

**Command:** `audioforge`
**Config:** `~/.audioforge/`

### Commands
```bash
audioforge generate <prompt>      # Generate SFX (alias: gen, g)
audioforge music <prompt>         # Generate music (alias: m)
audioforge compare <prompt>       # Compare providers
audioforge batch <file.yaml>      # Batch processing
audioforge convert <input>        # Convert format (wav, mp3, ogg, flac)
audioforge template save|list|show|delete
audioforge cost summary|pricing
audioforge providers
audioforge history list
```

### 4 Providers
| Provider | SFX | Music | Cost/gen |
|----------|-----|-------|----------|
| **ElevenLabs** | ✅ | ❌ | ~$0.007 |
| **Stability AI** | ✅ | ✅ (3 min) | ~$0.010 |
| **fal.ai** | ✅ | ✅ | ~$0.005-0.008 |
| **Replicate** | ✅ | ✅ | ~$0.007 |

### 15 Style Presets
`game-sfx`, `ui-click`, `ambient`, `cinematic`, `foley`, `8-bit`, `sci-fi`, `fantasy`, `horror`, `electronic`, `orchestral`, `lo-fi`, `notification`, `transition`, `nature`

### Duration Presets
- SFX: `blip` (0.5s), `short` (2s), `medium` (5s), `long` (10s), `extended` (30s)
- Music: `music-short` (15s), `music-medium` (30s), `music-long` (60s), `music-full` (180s)

### Music Options
`--genre` (ambient/electronic/orchestral/rock…), `--bpm`, `--instrumental`, `--loop`

### Game Audio Workflow
```bash
# UI sounds
audioforge gen "button click" --preset ui-click --duration blip
audioforge gen "menu hover" --preset ui-click --duration blip

# Game SFX
audioforge gen "sword slash" --preset game-sfx --duration short
audioforge gen "coin collect" --preset 8-bit --duration blip
audioforge gen "explosion" --preset game-sfx --duration medium
audioforge gen "level up fanfare" --preset game-sfx --duration short

# Background music
audioforge music "calm village theme" --genre ambient --loop --bpm 90
audioforge music "epic boss battle" --genre orchestral --loop --bpm 160
audioforge music "menu screen lo-fi" --genre electronic --loop --bpm 80
```

---

## 6. VideoForge — AI Video Generator

**Command:** `videoforge`
**Config:** `~/.videoforge/`

### Commands
```bash
videoforge generate <prompt>      # Text-to-video (alias: gen, g)
videoforge animate <image> [prompt] # Image-to-video (alias: anim, a)
videoforge compare <prompt>       # Compare providers
videoforge batch <file.yaml>      # Batch processing
videoforge convert <input> --to <format>  # Convert format (requires ffmpeg)
videoforge template save|list|show|delete
videoforge cost summary|pricing
videoforge providers list
videoforge history list
```

### 5 Providers
| Provider | Text→Video | Image→Video | Cost |
|----------|------------|-------------|------|
| **Runway** | ✅ | ✅ | $0.05-0.10/sec |
| **fal.ai** | ✅ | ✅ | Varies |
| **Replicate** | ✅ | ❌ | Varies |
| **Google Veo** | ✅ | ❌ | Varies |
| **OpenAI Sora** | ✅ | ❌ | Varies |

### 15 Style Presets
`cinematic`, `animation`, `vfx`, `product-demo`, `social-media`, `game-trailer`, `explainer`, `ambient`, `pixel-art`, `anime`, `3d-render`, `timelapse`, `glitch`, `minimal`, `watercolor`

### 11 Resolution Presets
`480p`, `720p`, `1080p`, `4k`, `phone` (1080×1920), `square`, `youtube`, `tiktok`, `story`, `og-video`, `tablet`

### Game/App Video Workflow
```bash
# Game trailer
videoforge generate "epic RPG game trailer with battles" --preset game-trailer --resolution youtube --duration 15

# App promo
videoforge generate "fitness app showcase" --preset product-demo --resolution phone --duration 10

# Social media
videoforge generate "app feature highlight" --preset social-media --resolution tiktok --duration 5
```

---

## 7. ScreenForge — App Store Asset Generator

**Command:** `screenforge`
**Config:** `~/.screenforge/config.json`

### Commands
```bash
screenforge icon <source>         # Generate 47 icon sizes from one 1024x1024 file
screenforge splash <source>       # Generate splash screens for all devices
screenforge frame <screenshot>    # Add device mockup frame
screenforge text <screenshot>     # Add marketing text overlay
screenforge meta generate         # AI-generate ASO metadata (title, description, keywords)
screenforge meta translate        # Translate metadata to another locale
screenforge batch <config.yaml>   # Batch processing
screenforge config set|list
```

### Icon Output (47 sizes)
- **iOS**: 13 sizes (1024px → 20px)
- **Android**: 6 sizes (512px → 108px)
- **Web/PWA**: 14 sizes (512px → 16px)
- **Favicon**: 3 sizes (48px, 32px, 16px)

### Splash Screen Output
- **iOS**: 8 sizes (iPhone 15 Pro Max → iPad Pro)
- **Android**: 5 sizes (xxxhdpi → mdpi)

### Device Frames
iPhone 15 Pro, iPhone 15, iPhone SE, iPad Pro 12.9", Pixel 8, Galaxy S24

### ASO Metadata (AI-generated)
- App title (max 30 chars), Subtitle (max 30 chars)
- Description (4000 chars), Keywords (10), Short description (80 chars)
- Supports translation to 200+ locales
- AI providers: Gemini (default) or OpenAI

### Notes
- Uses **Sharp.js** with Lanczos3 kernel — high quality
- SVG-based device frames — crisp at any resolution
- Output: `--format png` (default) or `webp`

---

## 8. TestForge — AI Test Generator

**Command:** `testforge`
**Config:** `~/.testforge/config.json`

### Commands
```bash
testforge scan [dir]              # Scan code without tests
testforge gen <file>              # AI-generate unit test
testforge gen:e2e "description"   # AI-generate E2E test from description
testforge coverage [dir]          # Show coverage report
testforge suggest [dir]           # AI suggest which tests to write first
testforge config set|list
```

### Supported Languages
| Language | Extensions | Test Frameworks | E2E Frameworks |
|----------|-----------|----------------|----------------|
| TypeScript | `.ts` | Jest, Vitest | Playwright |
| JavaScript | `.js` | Jest, Vitest | Playwright |
| Python | `.py` | pytest | — |
| Dart | `.dart` | flutter_test | — |
| Kotlin | `.kt` | JUnit | — |
| Swift | `.swift` | XCTest | — |

### AI Providers
- **OpenAI** (gpt-4o-mini default, supports gpt-4o)
- **Gemini** (gemini-2.0-flash default)

### Coverage Parsing
- Auto-detects: Istanbul JSON, lcov.info
- Shows color bar chart: green ≥80%, yellow ≥50%, red <50%

### Suggest Command
AI analyzes: complexity, risk/impact, function count → priorities: 🔴 high, 🟡 medium, 🔵 low

### Workflow
```bash
testforge scan ./src                          # Find untested code
testforge suggest ./src --limit 5             # AI suggests priorities
testforge gen src/critical-module.ts          # Generate tests for most critical file
testforge gen:e2e "user purchases item"       # Generate E2E test
testforge coverage . --min 80                 # Check coverage
```

---

## 9. MonForge — Production Monitoring

**Command:** `monforge`
**Config:** `~/.monforge/config.json`

### Commands
```bash
monforge status                   # Overall health dashboard
monforge errors [--limit N]       # Top errors from all providers
monforge reviews [platform]       # Reviews from App Store/Play Store
monforge report [--format md|txt] # Generate health report
monforge alerts set "metric op threshold"  # Set alert
monforge alerts list|remove
monforge config set|get|list
```

### 4 Monitoring Providers
| Provider | Function | Config keys |
|----------|----------|-------------|
| **Sentry** | Crash tracking, error monitoring | `sentry.authToken`, `sentry.orgSlug`, `sentry.projectSlug` |
| **Firebase Crashlytics** | Crash & ANR monitoring | `crashlytics.projectId`, `crashlytics.authToken` |
| **App Store Connect** | iOS reviews & ratings | `appstore.appId`, `appstore.issuerId`, `appstore.keyId`, `appstore.privateKey` |
| **Google Play** | Android reviews & ratings | `playstore.appId`, `playstore.keyFilePath` |

### Alert Metrics
- `crash-rate > 1` — Crash rate exceeds 1%
- `anr-rate > 0.5` — ANR rate exceeds 0.5%
- `rating < 4.0` — Rating below 4.0
- `error-count > 100` — Over 100 errors

### Operators
`>`, `<`, `>=`, `<=`, `==`, `!=`

---

## 10. StoreForge — App Store Deployment

**Command:** `storeforge`
**Config:** `~/.storeforge/config.json`

### Commands
```bash
storeforge init                   # Setup wizard for credentials
storeforge upload ios <ipa>       # Upload IPA to App Store
storeforge upload android <aab> [--track <track>]  # Upload AAB to Play Store
storeforge metadata sync <yaml>   # Sync metadata from YAML
storeforge metadata pull [-o file] # Pull current metadata
storeforge status                 # Check review status
storeforge release ios            # Submit iOS for review
storeforge release android [track] # Promote Android release
storeforge config set|get|list
```

### Upload Tracks (Android)
`internal` → `alpha` → `beta` → `production`

### Metadata YAML Format
```yaml
ios:
  en-US:
    title: "App Title"
    subtitle: "Subtitle"
    description: "Full description..."
    keywords: [keyword1, keyword2]
    whatsNew: "What's new in this version"

android:
  en-US:
    title: "App Title"
    shortDescription: "Short desc"
    description: "Full description..."
```

### Authentication
- **Apple**: JWT ES256 (Issuer ID + Key ID + .p8 private key)
- **Google**: OAuth2 RS256 (Service Account JSON)

### CI/CD Integration
```yaml
# GitHub Actions
- run: storeforge config set apple.issuerId ${{ secrets.APPLE_ISSUER_ID }}
- run: storeforge upload ios ./build/app.ipa
- run: storeforge upload android ./build/app.aab --track beta
- run: storeforge metadata sync metadata.yml
```

---

## Common Architecture (all Forge Tools)

### Directory Structure (unified)
```
src/
├── index.ts              # Entry point (Commander.js)
├── cli/commands/         # Command handlers
├── core/                 # Business logic, config, utilities
├── providers/            # Provider implementations (Strategy Pattern)
│   ├── base.ts          # Abstract base class
│   └── index.ts         # Registry & factory
└── types/               # TypeScript interfaces
```

### Shared Tech Stack
- **Commander.js** — CLI framework
- **Chalk** — Terminal colors
- **Ora** — Spinners
- **Inquirer** — Interactive prompts
- **YAML** — Config/batch parsing
- **TypeScript** (ES2022, strict, ESM)
- **Node.js 20+**

### Common Patterns
1. **Provider Strategy Pattern** — Abstract base → concrete implementations → factory
2. **Config Management** — `~/.<tool>/config.json` with dot-notation
3. **History Tracking** — JSON log with UUID entries
4. **Template System** — `{variable}` placeholders
5. **Batch Processing** — YAML/JSON config + `--dry-run`
6. **Cost Tracking** — Per-provider spending summaries
