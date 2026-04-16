---
name: forge-ecosystem
description: >-
  Forge Ecosystem — bộ 10 CLI tools cho phát triển game & app: appforge (scaffolding), backforge (backend), designforge (UI design AI), imgforge (image AI), audioforge (audio AI), videoforge (video AI), screenforge (store assets), testforge (test AI), monforge (monitoring), storeforge (deployment). Sử dụng khi cần scaffold dự án, tạo asset, test, deploy, hoặc monitor app/game.
---

# Forge Ecosystem Skill

## 🚨 ĐỌC TOÀN BỘ SKILL NÀY TRƯỚC KHI DÙNG BẤT KỲ FORGE TOOL NÀO

**KHÔNG ĐƯỢC chạy lệnh forge tool mà chưa đọc hết skill này.** Mỗi tool có flags, providers, và gotchas riêng — đoán sai sẽ gây lỗi.

Ngoài skill này, cũng đọc:
- `~/.github/copilot-instructions.md` — chỉ dẫn toàn cục (bắt buộc)
- `forge-tools/.github/instructions/marketing.instructions.md` — nếu tạo marketing assets
- `forge-tools/.github/instructions/landing.instructions.md` — nếu tạo landing page
- `<tool>/.github/instructions/development.instructions.md` — nếu sửa source code tool

---

Bộ 10 CLI tools TypeScript chuyên biệt cho toàn bộ vòng đời phát triển game và ứng dụng. Tất cả đều chạy trên Node.js 20+, dùng Commander.js, Chalk, Ora, Inquirer — có kiến trúc Provider Pattern thống nhất.

---

## Khi nào dùng Skill này

- Người dùng hỏi về cách scaffold dự án mới (game hoặc app)
- Cần tạo asset: hình ảnh, âm thanh, nhạc, video cho game/app
- Cần tạo UI design, extract design system
- Cần tạo test tự động bằng AI
- Cần tạo icon, splash screen, store metadata
- Cần deploy lên App Store / Google Play
- Cần monitoring production (crashes, errors, reviews)
- Cần bootstrap backend (Supabase, Firebase, PocketBase, Appwrite)

---

## 1. AppForge — Universal App Scaffolding

**Lệnh:** `appforge`
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

### Pipeline
Framework CLI (`create-expo-app`, `flutter create`, `create-next-app`) → Backend client → Auth → Styling → CI/CD → Copilot agents → README/Landing

---

## 2. BackForge — Backend Bootstrapper

**Lệnh:** `backforge`
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
Mỗi table → 3 interfaces: `User`, `UserInsert` (bỏ PK & defaults), `UserUpdate` (all optional)

### Provider Differences
| Provider | Schema Output | Seed Output | Deploy Command |
|----------|--------------|-------------|----------------|
| Supabase | SQL migrations | SQL INSERT | `supabase db push` |
| Firebase | Firestore rules | Firestore docs | `firebase deploy` |
| PocketBase | Collection JSON | Record counts | PocketBase admin |
| Appwrite | Collection JSON | Document counts | Appwrite CLI |

---

## 3. DesignForge — AI Design Generator

**Lệnh:** `designforge`
**Config:** `~/.designforge/`

### Commands
```bash
designforge generate <prompt>     # Tạo UI design từ text (alias: gen, g)
designforge edit <prompt>         # Sửa design có sẵn (-i input)
designforge vary <design-id>     # Tạo biến thể
designforge extract <file>       # Extract design tokens (colors, typography, spacing)
designforge batch <config.yaml>  # Batch processing
designforge compare <prompt>     # So sánh giữa providers
designforge template save|list|show|delete
designforge cost summary|pricing
designforge config set|list
```

### 4 Providers
| Provider | Output | Cost | Key | Trạng thái |
|----------|--------|------|-----|------------|
| **Google Stitch** | HTML, PNG | FREE | Cần (Labs key) | ⚠️ CLI 404 — dùng SDK |
| **Vercel v0** | React/JSX | $0.05/design | Cần | ✅ Hoạt động |
| **Figma API** | PNG, SVG, PDF | FREE | Cần | ✅ Hoạt động |
| **Canva Connect** | PNG, PDF | $0.01/design | Cần | ✅ Hoạt động |

### 12 Style Presets
`dashboard`, `landing`, `ecommerce`, `saas`, `mobile-app`, `blog`, `portfolio`, `social-media`, `email`, `onboarding`, `settings`, `auth`

### Device Types
- `mobile` (375×812), `desktop` (1440×900), `tablet` (768×1024), `agnostic`

### ⚠️ Google Stitch — QUAN TRỌNG
- **designforge CLI Stitch provider gọi REST API → luôn 404** (Google chưa public REST endpoint)
- **Phải dùng Stitch MCP Server hoặc `@google/stitch-sdk` trực tiếp**
- Env var: `STITCH_API_KEY` (Google Labs API key dạng `AQ.Ab8...`)
- **BUG SDK wrapper**: `project.generate()` crash vì `outputComponents[0]` là `designSystem`, KHÔNG phải `design.screens`
- Mỗi lần generate mất ~30-60 giây
- Stitch tự tạo design system riêng cho mỗi screen (không cần tạo trước)
- Prompt nên ghi rõ hex colors, font sizes, border radius, layout cụ thể
- Output: HTML (renderable) + PNG screenshot

### Stitch MCP Server (KHUYẾN NGHỊ cho AI agents)

Stitch MCP proxy đã cài global tại `~/.agents/mcp-servers/stitch-proxy.mjs`.
Đã cấu hình cho: **Cursor**, **IntelliJ Copilot**, **Gemini CLI**.

**Nếu IDE/agent có MCP support → gọi trực tiếp các tool sau (KHÔNG cần script):**

| MCP Tool | Mục đích | Params |
|----------|----------|--------|
| `create_project` | Tạo project | `title` |
| `list_projects` | Liệt kê projects | — |
| `generate_screen_from_text` | Generate UI screen | `projectId`, `prompt`, `deviceType` (MOBILE/DESKTOP/TABLET) |
| `edit_screens` | Sửa screen | `projectId`, `screenId`, `prompt` |
| `generate_variants` | Tạo biến thể | `projectId`, `screenId`, `prompt`, `variantCount` |
| `list_screens` | Liệt kê screens | `projectId` |
| `get_screen` | Chi tiết screen (image/HTML URLs) | `projectId`, `screenId`, `name` |
| `create_design_system` | Tạo design system | `projectId`, `title`, `content` |
| `apply_design_system` | Áp dụng DS | `projectId`, `designSystemId`, `screenIds` |

**Workflow chuẩn:**
1. `create_project` → lấy projectId
2. `generate_screen_from_text` (prompt chi tiết: hex colors, sizes, layout)
3. `list_screens` → lấy danh sách screens
4. `get_screen` → lấy image URL + HTML download URL
5. (Optional) `edit_screens` / `generate_variants` để refine

### Stitch Script (cho automation / batch — khi không có MCP)
```javascript
import { stitch } from "@google/stitch-sdk";
// KHÔNG dùng project.generate() — bị bug. Dùng callTool:
const raw = await stitch.callTool("generate_screen_from_text", {
  projectId: "PROJECT_ID", prompt: "...", deviceType: "MOBILE"
});
const screens = await stitch.callTool("list_screens", { projectId: "PROJECT_ID" });
const detail = await stitch.callTool("get_screen", {
  projectId: "PROJECT_ID", screenId: "SCREEN_ID",
  name: "projects/PROJECT_ID/screens/SCREEN_ID"
});
```

### Hỗ trợ khác (designforge CLI — cho v0/Figma/Canva)
- Hỗ trợ dark mode (`--dark`)
- Template với biến: `designforge template save app-screen "{page_type} page for {app_name}"`

---

## 4. ImgForge — AI Image Generator

**Lệnh:** `imgforge`
**Config:** `~/.imgforge/`

### Commands
```bash
imgforge generate <prompt>        # Tạo ảnh từ text (alias: gen, g)
imgforge edit <prompt>            # Sửa ảnh (-i input, --mask)
imgforge upscale <input>          # Phóng to 2x-4x
imgforge remove-bg <input>        # Xóa background → PNG trong suốt
imgforge vary <input>             # Tạo biến thể
imgforge batch <file.yaml>        # Batch processing
imgforge compare <prompt>         # So sánh providers
imgforge convert <input>          # Chuyển format (png, jpg, webp)
imgforge interactive              # Chế độ menu tương tác
imgforge template save|list|show|delete
imgforge cost summary|pricing
imgforge providers list
imgforge history list
```

### Generate Options (chính xác)
| Flag | Viết tắt | Mô tả | Mặc định |
|------|----------|-------|----------|
| `--provider` | `-p` | Provider (openai, gemini, stability, replicate, pollinations) | — |
| `--model` | `-m` | Model cụ thể | — |
| `--width` | `-W` | Chiều rộng pixel | 1024 |
| `--height` | `-H` | Chiều cao pixel | 1024 |
| `--ratio` | `-r` | Aspect ratio (9:16, 16:9, 1:1...) | — |
| `--preset` | `-s` | Style preset | — |
| `--output` | `-o` | File đầu ra | auto |
| `--format` | `-f` | Format (png, jpg, webp) | png |
| `--count` | `-n` | Số ảnh | 1 |
| `--quality` | `-q` | Chất lượng (standard, hd, ultra) | standard |
| `--negative` | — | Negative prompt | — |
| `--seed` | — | Seed cho reproducibility | — |
| `--enhance` | — | AI nâng cấp prompt | false |
| `--open` | — | Mở ảnh sau khi tạo | false |

### 5 Providers
| Provider | Models | Cost | API Key |
|----------|--------|------|---------|
| **Pollinations** | flux, flux-realism, flux-anime, turbo | **FREE** | Không cần |
| **Google Gemini** | Imagen 4/3, gemini-2.5-flash | $0.039-0.060 | Cần |
| **OpenAI** | DALL-E 3/2, gpt-image-1 | $0.04-0.12 | Cần |
| **Stability AI** | SD3, Stable Image Ultra/Core | $0.035-0.065 | Cần |
| **Replicate** | Flux Pro/Schnell, SDXL | $0.003-0.04 | Cần |

### 15 Style Presets
`photorealistic`, `anime`, `flat-design`, `watercolor`, `3d-render`, `neon`, `pixel-art`, `oil-painting`, `sketch`, `comic`, `minimalist`, `retro`, `cyberpunk`, `fantasy`, `isometric`

### 17 Aspect Ratios
`16:9`, `9:16`, `1:1`, `4:3`, `3:4`, `phone`, `og-image`, `instagram`, `story`, `portrait`, `landscape`, `twitter`, `facebook`, `linkedin`, `pinterest`, `youtube`, `tiktok`

### Quan trọng — Lưu ý thực tế (tránh lỗi)
- **Aspect ratio**: Dùng `-r 9:16` (KHÔNG phải `--aspect` — sẽ lỗi `unknown option`)
- **Custom size**: Dùng `-W <width> -H <height>` khi cần kích thước cụ thể
- **Style preset**: Dùng `-s` (KHÔNG phải `--preset`)
- **Replicate flux-1.1-pro**: Max height **1440px** — dùng `-W 816 -H 1440` cho mobile portrait
- **Replicate flux-schnell**: Nhanh (~2s), rẻ ($0.003), nhưng chất lượng thấp, file nhỏ
- **Pollinations FREE nhưng rate limit**: Dễ bị 429 khi chạy song song → chạy tuần tự
- **KHÔNG chạy song song** — rate limit 6 req/min (Replicate) → thêm `sleep 12` giữa mỗi request
- **`--enhance`** dùng AI nâng cấp prompt (thêm quality keywords)
- **`--seed`** cho kết quả reproducible
- **`--negative "text, watermark, logo"`** loại bỏ yếu tố không mong muốn
- Cho game: dùng presets `pixel-art`, `flat-design`, `isometric`, `anime`
- **Provider ưu tiên**: `replicate` với `flux-1.1-pro` cho production, `pollinations` cho draft
- **Kiểm tra provider**: `npx imgforge providers list` xem CONFIGURED hay NOT CONFIGURED
- **Prompt cho background**: `[subject] + [gradient colors] + [light effects] + [atmosphere] + "no text" + "8k quality"`

```bash
# ✅ ĐÚNG — tuần tự, đúng options, đúng max size
npx imgforge generate "dark gradient background" -p replicate -m black-forest-labs/flux-1.1-pro -W 816 -H 1440 -o bg.png
sleep 12
npx imgforge generate "character sprite" -p replicate -m black-forest-labs/flux-1.1-pro -r 1:1 -s pixel-art -o char.png

# ❌ SAI — options sai, song song
imgforge generate "bg" --aspect 9:16 --preset pixel-art  # --aspect không tồn tại
imgforge generate "a" -o a.png & imgforge generate "b" -o b.png &  # 429 rate limit
```

---

## 5. AudioForge — AI Audio Generator

**Lệnh:** `audioforge`
**Config:** `~/.audioforge/`

### Commands
```bash
audioforge generate <prompt>      # Tạo SFX (alias: gen, g)
audioforge music <prompt>         # Tạo nhạc (alias: m)
audioforge compare <prompt>       # So sánh providers
audioforge batch <file.yaml>      # Batch processing
audioforge convert <input>        # Chuyển format (wav, mp3, ogg, flac)
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
`--genre` (ambient/electronic/orchestral/rock...), `--bpm`, `--instrumental`, `--loop`

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

**Lệnh:** `videoforge`
**Config:** `~/.videoforge/`

### Commands
```bash
videoforge generate <prompt>      # Text-to-video (alias: gen, g)
videoforge animate <image> [prompt] # Image-to-video (alias: anim, a)
videoforge compare <prompt>       # So sánh providers
videoforge batch <file.yaml>      # Batch processing
videoforge convert <input> --to <format>  # Chuyển format (cần ffmpeg)
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

**Lệnh:** `screenforge`
**Config:** `~/.screenforge/config.json`

### Commands
```bash
screenforge icon <source>         # Tạo 47 icon sizes từ 1 file 1024x1024
screenforge splash <source>      # Tạo splash screens cho mọi device
screenforge frame <screenshot>   # Thêm device mockup frame
screenforge text <screenshot>    # Thêm marketing text overlay
screenforge meta generate        # AI tạo ASO metadata (title, description, keywords)
screenforge meta translate       # Dịch metadata sang locale khác
screenforge batch <config.yaml>  # Batch processing
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
- Hỗ trợ dịch sang 200+ locales
- AI providers: Gemini (mặc định) hoặc OpenAI

### Quan trọng
- Dùng **Sharp.js** với Lanczos3 kernel — chất lượng cao
- SVG-based device frames — crisp ở mọi resolution
- Output: `--format png` (mặc định) hoặc `webp`

---

## 8. TestForge — AI Test Generator

**Lệnh:** `testforge`
**Config:** `~/.testforge/config.json`

### Commands
```bash
testforge scan [dir]              # Scan code chưa test
testforge gen <file>              # AI tạo unit test
testforge gen:e2e "description"   # AI tạo E2E test từ mô tả
testforge coverage [dir]          # Hiển thị coverage report
testforge suggest [dir]           # AI gợi ý test nào nên viết trước
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
- **OpenAI** (gpt-4o-mini mặc định, hỗ trợ gpt-4o)
- **Gemini** (gemini-2.0-flash mặc định)

### Coverage Parsing
- Tự động detect: Istanbul JSON, lcov.info
- Hiển thị bar chart màu: xanh ≥80%, vàng ≥50%, đỏ <50%

### Suggest Command
AI phân tích: complexity, risk/impact, function count → ưu tiên: 🔴 high, 🟡 medium, 🔵 low

### Workflow
```bash
testforge scan ./src                          # Tìm code chưa test
testforge suggest ./src --limit 5             # AI gợi ý ưu tiên
testforge gen src/critical-module.ts          # Tạo test cho file quan trọng nhất
testforge gen:e2e "user purchases item"       # Tạo E2E test
testforge coverage . --min 80                 # Kiểm tra coverage
```

---

## 9. MonForge — Production Monitoring

**Lệnh:** `monforge`
**Config:** `~/.monforge/config.json`

### Commands
```bash
monforge status                   # Dashboard tổng quan health
monforge errors [--limit N]       # Top errors từ mọi provider
monforge reviews [platform]       # Reviews từ App Store/Play Store
monforge report [--format md|txt] # Tạo health report
monforge alerts set "metric op threshold"  # Đặt cảnh báo
monforge alerts list|remove
monforge config set|get|list
```

### 4 Monitoring Providers
| Provider | Chức năng | Config keys |
|----------|----------|-------------|
| **Sentry** | Crash tracking, error monitoring | `sentry.authToken`, `sentry.orgSlug`, `sentry.projectSlug` |
| **Firebase Crashlytics** | Crash & ANR monitoring | `crashlytics.projectId`, `crashlytics.authToken` |
| **App Store Connect** | iOS reviews & ratings | `appstore.appId`, `appstore.issuerId`, `appstore.keyId`, `appstore.privateKey` |
| **Google Play** | Android reviews & ratings | `playstore.appId`, `playstore.keyFilePath` |

### Alert Metrics
- `crash-rate > 1` — Crash rate vượt 1%
- `anr-rate > 0.5` — ANR rate vượt 0.5%
- `rating < 4.0` — Rating dưới 4.0
- `error-count > 100` — Quá 100 errors

### Operators
`>`, `<`, `>=`, `<=`, `==`, `!=`

---

## 10. StoreForge — App Store Deployment

**Lệnh:** `storeforge`
**Config:** `~/.storeforge/config.json`

### Commands
```bash
storeforge init                   # Setup wizard cho credentials
storeforge upload ios <ipa>       # Upload IPA lên App Store
storeforge upload android <aab> [--track <track>]  # Upload AAB lên Play Store
storeforge metadata sync <yaml>   # Đồng bộ metadata từ YAML
storeforge metadata pull [-o file] # Pull metadata hiện tại
storeforge status                 # Kiểm tra review status
storeforge release ios            # Submit iOS cho review
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

## Kiến trúc chung của tất cả Forge Tools

### Cấu trúc thư mục (thống nhất)
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

### Tech Stack chung
- **Commander.js** — CLI framework
- **Chalk** — Terminal colors
- **Ora** — Spinners
- **Inquirer** — Interactive prompts
- **YAML** — Config/batch parsing
- **TypeScript** (ES2022, strict, ESM)
- **Node.js 20+**

### Pattern chung
1. **Provider Strategy Pattern** — Abstract base → concrete implementations → factory
2. **Config Management** — `~/.<tool>/config.json` với dot-notation
3. **History Tracking** — JSON log với UUID entries
4. **Template System** — `{variable}` placeholders
5. **Batch Processing** — YAML/JSON config + `--dry-run`
6. **Cost Tracking** — Per-provider spending summaries
