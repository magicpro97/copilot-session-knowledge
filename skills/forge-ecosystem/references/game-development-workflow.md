# Game Development Workflow với Forge Ecosystem

## Quick Start — Tạo game mới

```bash
# 1. Scaffold
appforge init my-game    # Chọn: Flutter/Expo, Supabase, GitHub Actions

# 2. Backend cho leaderboard, user data
backforge init supabase
backforge schema define   # Tạo: users, scores, achievements, items
backforge schema apply
backforge types generate
backforge seed --count 50
```

---

## Asset Batch Configs

### game-sprites.yaml (cho imgforge)
```yaml
items:
  - prompt: "2D pixel art hero character idle pose, transparent background"
    preset: pixel-art
    ratio: square
    output: assets/sprites/hero-idle.png
  - prompt: "2D pixel art hero character running animation sheet"
    preset: pixel-art
    ratio: "16:9"
    output: assets/sprites/hero-run.png
  - prompt: "2D pixel art enemy slime idle pose, transparent background"
    preset: pixel-art
    ratio: square
    output: assets/sprites/enemy-slime.png
  - prompt: "game tilemap grass dirt stone water top-down view seamless"
    preset: flat-design
    ratio: square
    output: assets/tilesets/terrain.png
  - prompt: "game UI health bar mana bar pixel art"
    preset: pixel-art
    ratio: "16:9"
    output: assets/ui/bars.png
  - prompt: "game treasure chest open and closed pixel art"
    preset: pixel-art
    ratio: square
    output: assets/sprites/chest.png
```

### game-sounds.yaml (cho audioforge)
```yaml
items:
  # UI Sounds
  - prompt: "soft button click UI sound"
    preset: ui-click
    duration: 0.5
    output: assets/audio/sfx/ui-click.wav
  - prompt: "menu navigation hover sound gentle"
    preset: ui-click
    duration: 0.3
    output: assets/audio/sfx/ui-hover.wav

  # Combat SFX
  - prompt: "sword slash whoosh attack"
    preset: game-sfx
    duration: 1
    output: assets/audio/sfx/sword-slash.wav
  - prompt: "fireball magic spell cast explosion"
    preset: game-sfx
    duration: 2
    output: assets/audio/sfx/fireball.wav
  - prompt: "shield block metal clang defense"
    preset: game-sfx
    duration: 1
    output: assets/audio/sfx/shield-block.wav
  - prompt: "enemy death dissolve disappear"
    preset: game-sfx
    duration: 1.5
    output: assets/audio/sfx/enemy-death.wav

  # Collectibles
  - prompt: "coin collect chime retro 8-bit"
    preset: 8-bit
    duration: 0.5
    output: assets/audio/sfx/coin.wav
  - prompt: "power up gained sparkle magical"
    preset: game-sfx
    duration: 1.5
    output: assets/audio/sfx/powerup.wav
  - prompt: "level complete fanfare celebration short"
    preset: game-sfx
    duration: 3
    output: assets/audio/sfx/level-complete.wav

  # Ambient
  - prompt: "footsteps on grass outdoor walking"
    preset: foley
    duration: 2
    output: assets/audio/sfx/footsteps-grass.wav
  - prompt: "door open wooden creak"
    preset: foley
    duration: 1.5
    output: assets/audio/sfx/door-open.wav
```

### game-music.yaml (cho audioforge music)
```yaml
items:
  - prompt: "peaceful village theme with flute and acoustic guitar"
    genre: ambient
    bpm: 85
    loop: true
    duration: 60
    output: assets/audio/music/village-theme.wav
  - prompt: "tense dungeon exploration dark atmospheric"
    genre: ambient
    bpm: 100
    loop: true
    duration: 60
    output: assets/audio/music/dungeon-theme.wav
  - prompt: "epic boss battle intense orchestral drums"
    genre: orchestral
    bpm: 160
    loop: true
    duration: 60
    output: assets/audio/music/boss-battle.wav
  - prompt: "victory celebration triumphant brass fanfare"
    genre: orchestral
    bpm: 120
    duration: 15
    output: assets/audio/music/victory.wav
  - prompt: "main menu chill lo-fi beats relaxing"
    genre: electronic
    bpm: 80
    loop: true
    duration: 60
    output: assets/audio/music/menu-theme.wav
  - prompt: "game over melancholy piano sad short"
    genre: ambient
    bpm: 70
    duration: 10
    output: assets/audio/music/game-over.wav
```

---

## Game Schema Template (backforge)

```yaml
# game-schema.yaml
tables:
  - name: players
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: username, type: string, unique: true }
      - { name: email, type: string, unique: true }
      - { name: avatar_url, type: string }
      - { name: level, type: integer, default: "1" }
      - { name: experience, type: integer, default: "0" }
      - { name: coins, type: integer, default: "0" }
      - { name: created_at, type: timestamp, default: "now()" }

  - name: scores
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: player_id, type: uuid, references: players.id }
      - { name: score, type: integer }
      - { name: level_name, type: string }
      - { name: time_seconds, type: float }
      - { name: created_at, type: timestamp, default: "now()" }

  - name: achievements
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: name, type: string }
      - { name: description, type: text }
      - { name: icon_url, type: string }
      - { name: points, type: integer }

  - name: player_achievements
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: player_id, type: uuid, references: players.id }
      - { name: achievement_id, type: uuid, references: achievements.id }
      - { name: unlocked_at, type: timestamp, default: "now()" }

  - name: inventory_items
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: name, type: string }
      - { name: description, type: text }
      - { name: type, type: string }
      - { name: rarity, type: string }
      - { name: price, type: integer }
      - { name: icon_url, type: string }

  - name: player_inventory
    columns:
      - { name: id, type: uuid, primary: true }
      - { name: player_id, type: uuid, references: players.id }
      - { name: item_id, type: uuid, references: inventory_items.id }
      - { name: quantity, type: integer, default: "1" }
      - { name: acquired_at, type: timestamp, default: "now()" }
```

---

## Store Submission Checklist

```bash
# 1. Icon (cần file nguồn 1024x1024)
screenforge icon game-icon.png --platform all

# 2. Splash screen
screenforge splash game-logo.png --background "#0a0a2e"

# 3. Screenshots + device frames
screenforge frame screenshot-gameplay.png --device iphone-15-pro
screenforge frame screenshot-menu.png --device pixel-8

# 4. Marketing text trên screenshots
screenforge text framed-screenshot.png --text "Epic Adventures Await" --position top --color "#FFD700"

# 5. ASO metadata
screenforge meta generate -n "My Epic Game" -d "An RPG adventure game" -c games
screenforge meta translate -i metadata.json -l ja  # Japanese
screenforge meta translate -i metadata.json -l ko  # Korean

# 6. Game trailer
videoforge generate "epic RPG mobile game trailer showing battles exploration and character customization" \
  --preset game-trailer --resolution youtube --duration 15

# 7. Upload & deploy
storeforge metadata sync metadata.yml
storeforge upload ios ./build/MyGame.ipa
storeforge upload android ./build/MyGame.aab --track internal

# 8. Promote through tracks
storeforge release android beta
storeforge release android production
storeforge release ios
```

---

## Monitoring sau khi launch

```bash
# Setup alerts
monforge config set sentry.authToken "your-token"
monforge config set sentry.orgSlug "your-org"
monforge config set sentry.projectSlug "my-game"
monforge alerts set "crash-rate > 0.5"
monforge alerts set "rating < 4.0"
monforge alerts set "anr-rate > 0.3"

# Daily check
monforge status
monforge errors --limit 5
monforge reviews all
monforge report --format md --output weekly-report.md
```
