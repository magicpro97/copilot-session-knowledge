---
name: forge-ecosystem
description: >-
  Forge Ecosystem — 10 CLI tools for game & app development. Use when scaffolding projects, generating AI assets (images, audio, video), creating UI designs, running AI tests, deploying to app stores, or monitoring production. Tools: appforge, backforge, designforge, imgforge, audioforge, videoforge, screenforge, testforge, monforge, storeforge.
---

# Forge Ecosystem Skill

Bộ 10 CLI tools TypeScript chuyên biệt cho toàn bộ vòng đời phát triển game và ứng dụng. Tất cả đều chạy trên Node.js 20+, dùng Commander.js, Chalk, Ora, Inquirer — có kiến trúc Provider Pattern thống nhất.

Before using any forge tool, check `references/tools-reference.md` for detailed flags, providers, and per-tool gotchas.

Also read:
- `references/app-development-workflow.md` — full app dev workflow
- `references/game-development-workflow.md` — full game dev workflow
- `~/.github/copilot-instructions.md` — global instructions
- `forge-tools/.github/instructions/marketing.instructions.md` — if generating marketing assets

---

## When to Use (Khi nào dùng Skill này)

Use this skill when:
- Scaffolding a new game or app project (`appforge`)
- Generating assets: images, audio, music, video for game/app (`imgforge`, `audioforge`, `videoforge`)
- Creating UI designs or extracting design systems (`designforge`)
- Generating AI-powered tests (`testforge`)
- Creating icons, splash screens, store metadata (`screenforge`)
- Deploying to App Store / Google Play (`storeforge`)
- Monitoring production crashes, errors, and reviews (`monforge`)
- Bootstrapping backend (Supabase, Firebase, PocketBase, Appwrite) (`backforge`)

---

## Tool Quick Reference

| Tool | Command | Purpose |
|------|---------|---------|
| AppForge | `appforge` | Universal app scaffolding (Expo, Flutter, Next.js, Vite, Astro) |
| BackForge | `backforge` | Backend bootstrapping (Supabase, Firebase, PocketBase, Appwrite) |
| DesignForge | `designforge` | AI UI design generator (v0, Figma, Canva; Stitch via MCP) |
| ImgForge | `imgforge` | AI image generator (Replicate, OpenAI, Gemini, Stability, Pollinations) |
| AudioForge | `audioforge` | AI audio/SFX/music generator (ElevenLabs, Stability, fal.ai, Replicate) |
| VideoForge | `videoforge` | AI video generator (Runway, fal.ai, Replicate, Veo, Sora) |
| ScreenForge | `screenforge` | App store assets: 47 icon sizes, splash screens, ASO metadata |
| TestForge | `testforge` | AI test generator (Jest, Vitest, pytest, XCTest, JUnit, Playwright) |
| MonForge | `monforge` | Production monitoring (Sentry, Crashlytics, App Store, Play Store) |
| StoreForge | `storeforge` | App store deployment (App Store Connect, Google Play) |

---

## Critical Gotchas (read before running any command)

### ImgForge
- Use `-r 9:16` for aspect ratio, NOT `--aspect` (unknown option error)
- Use `-s` for style preset, NOT `--preset`
- Replicate max height: 1440px → use `-W 816 -H 1440` for mobile portrait
- **Never run parallel requests** — 6 req/min rate limit → add `sleep 12` between requests
- Pollinations is FREE but prone to 429 → run sequentially

### DesignForge / Google Stitch
- `designforge` CLI Stitch provider → always 404 (REST endpoint not public)
- Use Stitch MCP Server (`~/.agents/mcp-servers/stitch-proxy.mjs`) instead
- SDK `project.generate()` crashes — use `stitch.callTool("generate_screen_from_text", {...})`

---

## Common Workflows

### New App Project
```bash
appforge init my-app
# → wizard: framework, backend, auth, styling, CI/CD, agents, README, landing
```

### Generate Game Assets
```bash
# Background image
npx imgforge generate "dark fantasy dungeon" -p replicate -m black-forest-labs/flux-1.1-pro -W 816 -H 1440 -s fantasy -o bg.png
sleep 12

# Game SFX
audioforge gen "sword clash metal" --preset game-sfx --duration short

# Background music
audioforge music "epic dungeon theme" --genre orchestral --loop --bpm 120

# Game trailer
videoforge generate "2D RPG game trailer" --preset game-trailer --resolution youtube --duration 15
```

### App Store Submission
```bash
# 1. Generate all icon sizes
screenforge icon ./icon-1024.png

# 2. AI metadata
screenforge meta generate

# 3. Upload
storeforge upload ios ./build/app.ipa
storeforge upload android ./build/app.aab --track beta
```

### Production Monitoring Setup
```bash
monforge config set sentry.authToken YOUR_TOKEN
monforge status          # health dashboard
monforge errors --limit 10
```

---

## Architecture (all tools share this pattern)

```
src/
├── index.ts              # Commander.js entry
├── cli/commands/         # Command handlers
├── core/                 # Business logic, config, utilities
├── providers/            # Strategy Pattern implementations
└── types/                # TypeScript interfaces
```

**Common patterns:** Provider Strategy, `~/.<tool>/config.json` dot-notation, JSON history log, `{variable}` template system, YAML batch processing with `--dry-run`, per-provider cost tracking.

---

<example>
User: "I need to create a mobile game — scaffold it with Expo + Supabase and generate some initial assets."

1. Scaffold the project:
   ```bash
   appforge init my-mobile-game
   # Select: Expo → Supabase → Email auth → NativeWind → Zustand → GitHub Actions
   ```

2. Generate character sprite:
   ```bash
   npx imgforge generate "pixel art warrior character, white background, 1:1 ratio" \
     -p replicate -m black-forest-labs/flux-1.1-pro -r 1:1 -s pixel-art -o warrior.png
   sleep 12
   ```

3. Generate button click SFX:
   ```bash
   audioforge gen "8-bit button click" --preset ui-click --duration blip
   ```

4. Generate app icon set:
   ```bash
   screenforge icon ./warrior-1024.png
   # → 47 sizes for iOS + Android + Web
   ```

See `references/tools-reference.md` for full flags and provider details.
</example>

<example>
User: "Generate marketing assets for our fitness app — screenshots with device frames and App Store metadata."

1. Frame screenshots with device mockup:
   ```bash
   screenforge frame screenshot-home.png  # iPhone 15 Pro frame
   screenforge frame screenshot-workout.png
   ```

2. Add marketing overlay text:
   ```bash
   screenforge text screenshot-home-framed.png
   ```

3. AI-generate App Store metadata:
   ```bash
   screenforge meta generate
   # → title, subtitle, description, keywords in en-US
   ```

4. Upload when ready:
   ```bash
   storeforge upload ios ./build/fitness-app.ipa
   storeforge metadata sync metadata.yml
   storeforge release ios
   ```
</example>
