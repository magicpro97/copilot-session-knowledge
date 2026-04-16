---
title: "Forge Ecosystem — CLI tools cho game & app"
applyTo: "**/forge-*/**,**/imgforge*,**/audioforge*,**/videoforge*,**/screenforge*,**/designforge*,**/backforge*,**/testforge*,**/monforge*,**/storeforge*,**/appforge*"
---

# Forge Ecosystem

Bộ 10 CLI tools cho toàn bộ vòng đời phát triển game & app.

## Tools Overview

| Tool | Mục đích | Lệnh chính |
|------|----------|------------|
| **appforge** | Scaffolding dự án | `appforge init`, `appforge add` |
| **backforge** | Bootstrap backend | `backforge init`, `backforge schema`, `backforge seed` |
| **designforge** | Tạo UI design bằng AI | `designforge generate`, `designforge extract` |
| **imgforge** | Tạo hình ảnh AI | `imgforge generate`, `imgforge edit`, `imgforge upscale` |
| **audioforge** | Tạo âm thanh/nhạc AI | `audioforge gen`, `audioforge music` |
| **videoforge** | Tạo video AI | `videoforge generate`, `videoforge animate` |
| **screenforge** | Tạo icon, splash, store assets | `screenforge icon`, `screenforge splash`, `screenforge meta` |
| **testforge** | Tạo test tự động bằng AI | `testforge scan`, `testforge gen` |
| **monforge** | Monitoring production | `monforge status`, `monforge errors`, `monforge alerts` |
| **storeforge** | Deploy lên App Store/Play Store | `storeforge upload`, `storeforge release` |

## Lưu ý QUAN TRỌNG (tránh lỗi)

### imgforge
- **Aspect ratio**: Dùng `-r 9:16` (KHÔNG phải `--aspect`)
- **Custom size**: Dùng `-W <width> -H <height>`
- **Provider ưu tiên**:
  - `replicate` (CONFIGURED) — production quality
  - `pollinations` (FREE) — draft/test nhanh
- **Model ưu tiên**:
  - `black-forest-labs/flux-1.1-pro` — chất lượng cao, **max height 1440px**
  - `black-forest-labs/flux-schnell` — nhanh, rẻ ($0.003)
- **Rate limit**: Replicate giới hạn 6 req/min → **thêm `sleep 12` giữa các request**
- **KHÔNG chạy song song** → rate limit 429

```bash
# ✅ ĐÚNG — tuần tự với cooldown
imgforge generate "prompt 1" -p replicate -m black-forest-labs/flux-1.1-pro -W 816 -H 1440 -o img1.png
sleep 12
imgforge generate "prompt 2" -p replicate -m black-forest-labs/flux-1.1-pro -W 816 -H 1440 -o img2.png
```

### designforge
- ⚠️ Google Stitch qua CLI **KHÔNG hoạt động** (REST 404) → dùng Stitch MCP trực tiếp
- Alternatives qua CLI: v0 (React, $0.05), Figma (free, cần key), Canva ($0.01)

### Tất cả forge tools
- Dùng `npx <tool>` thay vì global install
- Chạy `npx <tool> --help` trước khi dùng
- Batch processing: YAML config + `--dry-run` trước

## Provider ưu tiên

| Tool | Production | Draft/Test |
|------|-----------|-----------|
| imgforge | replicate flux-1.1-pro (~$0.04) | pollinations (free) |
| designforge | Stitch MCP (free) | v0 ($0.05) |
| backforge | Supabase | PocketBase |

## Quy trình phát triển chuẩn

```
appforge init → backforge init → [Code] → testforge scan/gen → screenforge → storeforge → monforge
```

## Config Storage
Forge tools lưu config: `~/.<tool>/config.json`

## Batch Processing
Luôn dùng YAML config cho batch operations:
```yaml
items:
  - prompt: "sword icon 64x64"
    preset: pixel-art
    output: assets/icons/sword.png
```
