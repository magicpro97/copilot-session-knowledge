# Global Copilot Instructions

> Harness Engineering principles. Project-specific rules go in `{project}/.github/copilot-instructions.md`.
> Scope-specific details are in `~/.github/instructions/*.instructions.md`.

## 🛡️ Harness Engineering — 7 Nguyên tắc

1. **KHÔNG ship lỗi**: CODE → COMPILE → TEST → VERIFY → COMMIT. Không compile = không commit. 100% hoặc 0%.
2. **Bám workflow**: Đọc WORKFLOW.md → xác định phase → plan.md → implement. Không có workflow → PLAN → BUILD → TEST → VERIFY → COMMIT.
3. **Perfect pixel**: Screenshot trước/sau. Verify text, color hex, spacing dp, touch ≥44dp. Design-first.
4. **Orchestrate — Tentacle**: Task ≥3 files, ≥2 modules → **BẮT BUỘC** dùng `tentacle-orchestration`. Decompose → parallel agents → verify. Ưu tiên `tentacle.py ... --briefing` để tạo runtime bundle + structured `[KNOWLEDGE EVIDENCE]`; `--for-subagent` chỉ dùng manual compatibility/ad hoc prompts. Single-module nhỏ → làm trực tiếp.
   Structured memory contracts stay fixed: `query-session.py --task --export json` surfaces `entries[]`;
   `briefing.py --task --json` surfaces `tagged_entries[]` / `related_entries[]`; `briefing.py --pack`
   surfaces `entries.<category>[]`. Phase 5 telemetry is lean: `knowledge-health.py --recall`
   (`--json` optional) is recall-only; `query-session.py --detail` logs stateless `detail_open`
   (`hit_count=1` only when found, miss = `hit_count=0`); default `query-session` telemetry counts
   the full emitted search surface.
   **Workflow**: `create` → `todo add` → `swarm/dispatch --briefing` (bundle-first by default) → `complete` (verification/closure). Operator view: `tentacle.py status`. Opt out with `--no-bundle` only for tiny/manual prompts.
5. **Không bỏ dở**: Fix or delegate, never abandon. Context limit → checkpoint + delegate.
6. **AGENTS.md**: Mọi project nên có. <60 dòng, navigational. Đọc nó trước.
7. **Ghi nhận**: Sau mỗi task → `learn.py` mistakes/patterns. Knowledge = long-term memory.

## 🔍 Code Navigation — LSP FIRST

1. **LSP** → cho MỌI code symbols (definition, references, callers)
2. **glob** → tìm file theo tên
3. **grep** → CHỈ KHI LSP vô dụng (literal text, config, comments)

❌ KHÔNG `grep "fun myFunction"` — phải dùng LSP

## 🚨 KHÔNG ĐƯỢC

- Bỏ qua instructions rồi dùng kiến thức cũ
- Đoán cú pháp CLI → đọc skills/instructions
- Gọi Stitch qua designforge CLI → 404 (dùng Stitch MCP)
- Dùng `--aspect` thay `-r` cho imgforge → crash
- Gắn `trend-scout.py` vào `preToolUse`/`postToolUse` hooks (gây spam) — dùng workflow `trend-scout.yml` hoặc chạy tay

## 📋 Scope-specific Instructions

Chi tiết nằm trong `~/.github/instructions/`:
- `forge-ecosystem.instructions.md` — Forge tools, providers, gotchas
- `stitch-design.instructions.md` — Stitch MCP workflow, tokens block
- `game-dev.instructions.md` — Game asset pipeline
- `app-dev.instructions.md` — App UI/UX, store deploy

## 🔄 Sync Rollout (local-first, optional)

- Sync dùng mô hình local-first: local `knowledge.db` vẫn là nguồn đọc chính.
- Cấu hình chỉ có **một** `connection_string` trong `~/.copilot/tools/sync-config.json`.
- Lệnh runtime/diagnostics:
  - `python3 ~/.copilot/tools/sync-config.py --setup <url>|--setup-env <ENV_VAR>|--status|--status --json|--get|--clear`
  - `python3 ~/.copilot/tools/sync-daemon.py --once|--daemon|--interval <seconds>|--push-only|--pull-only`
  - `python3 ~/.copilot/tools/sync-status.py [--json]|--watch-status [--json]|--health-check [--json]|--audit [--json]`
  - `python3 ~/.copilot/tools/auto-update-tools.py --restart-watch|--watch-status|--health-check|--audit-runtime`
- Không có `connection_string` ⇒ sync daemon local-only/idle (không fail cứng).
- Hardening runtime: daemon tự tăng giới hạn sync khi backlog lớn, pull nhiều page trong một cycle, và refresh `knowledge_fts` / `ke_fts` ngay sau pull apply.
- `sync-gateway.py` chỉ là **reference/mock** contract surface (`/sync/push`, `/sync/pull`, `/healthz`), không phải production authority.
- `sync-config.py --setup` nhận URL HTTP(S) gateway, không nhận trực tiếp DSN Postgres/libSQL.
- Khuyến nghị rollout mặc định cho provider-backed gateway: Neon (Postgres backend) + Railway (host gateway mỏng); đây là khuyến nghị mặc định, không khóa vendor.
- Browse diagnostics là read-only: `/healthz` công bố `/api/sync/status`; endpoint này báo trạng thái queue/failure/config/cursor local.

## 🔧 Skills

Đọc skills liên quan trong `~/.copilot/skills/` (global) hoặc `.github/skills/` (project).

| Skill | Khi nào dùng |
|-------|-------------|
| `find-skills` | Tìm & cài skill mới từ skills.sh |
| `agent-instructions-auditor` | Sau khi edit instruction files — audit token budget, cache safety, quality |
| `tentacle-orchestration` | Task ≥3 files ≥2 modules → decompose → parallel agents |
