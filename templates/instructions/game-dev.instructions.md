---
title: "Game Development Pipeline"
applyTo: "**/game*/**,**/assets/**,**/sprites/**,**/sfx/**"
---

# Game Development Pipeline

## Asset Pipeline
```bash
# Sprites & textures (tuần tự, sleep giữa mỗi request)
imgforge generate "2D pixel art character" -p replicate -m black-forest-labs/flux-1.1-pro -r 1:1 -s pixel-art -o character.png
sleep 12
imgforge generate "tilemap grass stone water" -p replicate -m black-forest-labs/flux-1.1-pro -r 1:1 -s flat-design -o tilemap.png

# Sound effects
audioforge gen "coin collect" --preset 8-bit --duration blip
audioforge gen "level complete fanfare" --preset game-sfx --duration short

# Background music
audioforge music "calm exploration music" --genre ambient --loop --duration 60

# Batch
audioforge batch game-sounds.yaml
imgforge batch game-sprites.yaml
```

## Game-specific Presets
- **imgforge**: `pixel-art`, `flat-design`, `3d-render`, `anime`, `watercolor`
- **audioforge**: `game-sfx`, `8-bit`, `ambient`, `cinematic`, `electronic`, `fantasy`, `sci-fi`
- **videoforge**: `game-trailer`, `pixel-art`, `animation`, `vfx`
