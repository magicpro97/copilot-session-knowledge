---
title: "Google Stitch MCP — UI Design"
applyTo: "**/stitch*,**/design*,**/*.html"
---

# Google Stitch MCP — Tạo UI Design

⚠️ **designforge CLI Stitch provider → REST 404. Phải dùng Stitch MCP.**

## MCP Server Config
- `~/.copilot/mcp-config.json` → Copilot CLI
- `~/.cursor/mcp.json` → Cursor
- `~/.gemini/antigravity/mcp_config.json` → Gemini CLI

## 12 MCP Tools
| Tool | Mục đích |
|------|----------|
| `create_project` | Tạo project mới |
| `get_project` / `list_projects` | Lấy/liệt kê projects |
| `generate_screen_from_text` | Generate screen từ prompt |
| `edit_screens` | Sửa screen(s) bằng prompt |
| `generate_variants` | Tạo biến thể design |
| `list_screens` / `get_screen` | Liệt kê/lấy chi tiết screen |
| `create_design_system` | Tạo design system |
| `update_design_system` | Cập nhật design system |
| `list_design_systems` | Liệt kê DS trong project |
| `apply_design_system` | Áp dụng DS cho screens |

## ⚠️ Hành vi CRITICAL (Rút từ audit thực tế)

1. **Mỗi `generate_screen_from_text` tạo Design System MỚI** — font NGẪU NHIÊN nếu prompt không chỉ rõ
2. **`apply_design_system` chỉ thay COLOR** — KHÔNG sửa CSS `font-family`
3. **`edit_screens` tạo screen VERSION MỚI** (ID mới) — instance cũ vẫn tồn tại
4. **Không có API xoá screen** — chỉ xoá qua Web UI
5. **HTML width 780px = 390px mobile** (2x render)

## Mandatory Design Tokens Block

**PHẢI có ở đầu MỌI prompt** gửi cho Stitch:

```
=== MANDATORY DESIGN TOKENS (DO NOT DEVIATE) ===
Font: "Inter" for ALL text — headlines, body, labels, numbers. No other font family.
Mobile: 390×844 viewport (390px CSS width).
Background: linear-gradient(to bottom, #0D0D2B, #1A1B3D, #2D2F6B)
Glass cards: background rgba(255,255,255,0.07), border 1px rgba(255,255,255,0.15), border-radius 20px, backdrop-filter blur(16px), padding 20px
Colors: Primary #7C4DFF, Cyan #00E5FF, Pink #FF6090, Gold #FFD740
Text colors: Primary #E6E1F9, Secondary #BDB6D0
Type scale: Display 57px light, Title 28px medium, Subtitle 20px medium, Body 14px normal, Label 12px medium, Small 11px medium
Buttons: Primary filled (cyan #00E5FF bg, black text), Secondary outlined (1px cyan border), height 48-56px, radius 20px
Shapes: Small 12px, Medium 20px (default), Large 28px, FAB 32px
=== END TOKENS ===
```

## Workflow chuẩn
```
1. create_project → lấy projectId
2. create_design_system → Inter, ROUND_TWELVE, DARK, #7C4DFF
3. generate_screen_from_text (tuần tự) — MỖI prompt BẮT ĐẦU bằng tokens block
4. Sau mỗi batch → apply_design_system cho tất cả screens
5. Download HTML → verify fonts = Inter, sizes đúng type scale
6. Nếu sai font → edit_screens để fix
```

## Prompt Tips
- **Luôn** specify `font-family "Inter"` — KHÔNG dùng "system font"
- Ghi rõ **hex color codes** (#00E5FF, không phải "cyan")
- Specify **390×844** (không phải 375×812)
- Mô tả **layout top-to-bottom** với px sizes cụ thể
- 1-2 thay đổi mỗi iteration
