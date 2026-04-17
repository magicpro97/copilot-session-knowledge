# Global Copilot Instructions

> Harness Engineering principles. Project-specific rules go in `{project}/.github/copilot-instructions.md`.
> Scope-specific details are in `~/.github/instructions/*.instructions.md`.

## 🧠 Session Knowledge — BẮT BUỘC

**TRƯỚC KHI bắt đầu bất kỳ task phức tạp nào**, chạy briefing:

```bash
python3 ~/.copilot/tools/briefing.py "mô tả task" --full
```

- **Sub-agents**: `python3 ~/.copilot/tools/briefing.py "task" --for-subagent` → inject vào prompt
- **Gặp lỗi**: `python3 ~/.copilot/tools/query-session.py "error message" --verbose`
- **Giữa công việc**: `python3 ~/.copilot/tools/query-session.py "pattern hoặc keyword"` — tra cứu KB khi gặp vấn đề quen hoặc cần pattern đã dùng trước
- **Sau khi fix**: `python3 ~/.copilot/tools/learn.py --mistake "Tiêu đề" "Mô tả"`
- **Trước commit/task_complete**: `python3 ~/.copilot/tools/learn.py` — BẮT BUỘC ghi nhận nếu ≥3 file code đã sửa (hook sẽ BLOCK nếu chưa gọi)

✅ Luôn briefing trước task phức tạp | ✅ Luôn search KB khi gặp lỗi | ✅ Luôn ghi nhận mistakes/patterns
❌ KHÔNG bỏ qua briefing | ❌ KHÔNG debug từ đầu khi KB đã có solution | ❌ KHÔNG commit/task_complete mà chưa learn
⚠️ Hooks phát hiện file writes qua bash (heredoc, redirect, sed, cp...) — không bypass được

## 🛡️ Harness Engineering — 7 Nguyên tắc

1. **KHÔNG ship lỗi**: CODE → COMPILE → TEST → VERIFY → COMMIT. Không compile = không commit. 100% hoặc 0%.
2. **Bám workflow**: Đọc WORKFLOW.md → xác định phase → plan.md → implement. Không có workflow → PLAN → BUILD → TEST → VERIFY → COMMIT.
3. **Perfect pixel**: Screenshot trước/sau. Verify text, color hex, spacing dp, touch ≥44dp. Design-first.
4. **Orchestrate — Tentacle**: Task ≥3 files, ≥2 modules → **BẮT BUỘC** dùng `tentacle-orchestration`. Decompose → parallel agents → verify. Inject `--for-subagent`. Single-module nhỏ → làm trực tiếp.
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

## 📋 Scope-specific Instructions

Chi tiết nằm trong `~/.github/instructions/`:
- `forge-ecosystem.instructions.md` — Forge tools, providers, gotchas
- `stitch-design.instructions.md` — Stitch MCP workflow, tokens block
- `game-dev.instructions.md` — Game asset pipeline
- `app-dev.instructions.md` — App UI/UX, store deploy

## 🔧 Skills

Đọc skills liên quan trong `~/.copilot/skills/` hoặc `~/.agents/skills/`.

| Skill | Khi nào dùng |
|-------|-------------|
| `find-skills` | Tìm & cài skill mới từ skills.sh |
| `agent-instructions-auditor` | Sau khi edit instruction files — audit token budget, cache safety, quality |
| `tentacle-orchestration` | Task ≥3 files ≥2 modules → decompose → parallel agents |
