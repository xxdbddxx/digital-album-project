# HANDOFF — 多模态用户语音情绪增强

## 当前状态

- 交接日期：2026-06-25（Asia/Shanghai）
- 接手对象：Google Antigravity
- 仓库：`C:\Users\sxxy4\Documents\esp_projects\digital-album-project`
- 隔离 worktree：`C:\Users\sxxy4\Documents\esp_projects\digital-album-project\.worktrees\multimodal-user-emotion`
- 开发分支：`codex/multimodal-user-emotion`
- 当前 HEAD：`99c6e3d chore: ignore superpowers progress state`
- `main` 基点：`e9d2b52 chore: ignore local worktrees`
- 工作区状态：干净；尚未开始功能代码开发。

由于官方额度只剩约 5%，已停止所有新开发和子代理。Task 1 子代理仅阅读上下文，未修改文件、未运行测试、未创建提交。

## 已完成并确认的产物

1. 设计规格：
   - `docs/superpowers/specs/2026-06-25-user-emotion-design.md`
   - 提交：`ebcff73 docs: design multimodal user emotion enhancement`
2. TDD 实施计划：
   - `docs/superpowers/plans/2026-06-25-user-emotion.md`
   - 提交：`9948ca8 docs: plan multimodal user emotion enhancement`
3. worktree 安全设置：
   - `main` 提交 `e9d2b52` 将 `.worktrees/` 加入 `.gitignore`
   - 分支提交 `99c6e3d` 将 `.superpowers/` 加入 `.gitignore`
4. 基线测试：

```powershell
& 'C:\Users\sxxy4\Documents\esp_projects\digital-album-project\backend\.venv\Scripts\python.exe' -m unittest discover -s backend\tests -v
```

结果：4/4 通过，均为现有 `test_music_routing.py` 测试。

## 用户已确认的关键决策

- 这是现有稳定语音模块的旁路增强，非必要不改原模块。
- 双专家融合：云端声学专家 + 现有 DeepSeek 语义专家 + 本地确定性融合器。
- 原始音频允许上传云端；比赛优先，隐私暂不是约束。
- 声学结果与 Whisper 并行；ASR 完成后最多等待 400ms。
- 超时不得阻塞回复；迟到结果只更新 UI/会话状态，不得追发硬件动作。
- 用户情绪 8 类：`joy/sadness/anxiety/anger/fatigue/loneliness/calm/neutral`。
- 只做会话内 3–5 轮平滑，结束后清空，不跨会话保存。
- UI、回复/TTS、硬件采用不同置信度门槛。
- 比赛 `demo_mode=true`：双专家一致且融合置信度 ≥0.80，可自动执行一次干预。
- 显式用户指令始终优先。
- 情绪音乐保持非循环；语音和音乐默认音量保持 60%。
- 明确指定但不存在的歌曲/照片不得随机兜底。

## 不得破坏的现有约束

- 主链路：`INMP441 → WakeNet → VAD → WebSocket → faster-whisper CUDA → DeepSeek V4 Flash → Aliyun CosyVoice → MAX98357A`。
- WakeNet 增益 24x；录音增益 12x；语音后静音约 1.2s 结束。
- AEC 接收输出增益前的线性麦克风数据；音量缩放后的播放参考必须先入队再写 I2S DMA。
- 持久语音 WebSocket；任务栈和传输 buffer 都是 8192 字节。
- 回复文本必须先发设备，再生成和流式发送对应 TTS 句子。
- 连续对话等待不能阻塞设备命令轮询。
- 音乐必须可被“你好小智”打断。
- 本板无 MPU6050；启动 440Hz 自动扬声器测试必须保持禁用。
- 不使用 PlatformIO；ESP-IDF 命令必须走 `scripts\idf.ps1`。
- 不运行 `fullclean`，除非证明构建状态陈旧。

## 推荐首个云端声学 Provider

计划锁定：

- 模型：`qwen3-omni-30b-a3b-captioner`
- Endpoint：`https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
- Key：复用 `DASHSCOPE_API_KEY`，只存 `backend\.env.local`
- 官方参考：<https://help.aliyun.com/zh/model-studio/qwen3-omni-captioner>

注意：实现前重新核对当前官方请求格式。计划假设本地 16k mono s16 PCM 封装 WAV 后，以 Base64 `input_audio` 提交。

## 接手执行顺序

严格按 `docs/superpowers/plans/2026-06-25-user-emotion.md` 执行：

1. Task 1：情绪类型与配置契约
2. Task 2：确定性融合与会话平滑/干预状态
3. Task 3：DashScope 声学情绪适配器
4. Task 4：回复风格与硬件授权策略
5. Task 5：语义情绪协议与流式提取
6. Task 6：并行接入现有语音管线
7. Task 7：指标与回放评估工具
8. Task 8：全量回归与 ESP-IDF 构建

每个任务必须遵循 TDD：先写测试并看到预期失败，再写最小实现，测试通过后提交。不要把多个任务压成一个大提交。

## 当前可立即执行的 Task 1

Task 1 尚未开始。所有要求在计划文档中。预期文件：

- 新建 `backend/services/emotion/__init__.py`
- 新建 `backend/services/emotion/models.py`
- 新建 `backend/tests/test_emotion_models.py`
- 修改 `backend/.env.example`

测试解释器：

```powershell
C:\Users\sxxy4\Documents\esp_projects\digital-album-project\backend\.venv\Scripts\python.exe
```

建议先执行：

```powershell
cd C:\Users\sxxy4\Documents\esp_projects\digital-album-project\.worktrees\multimodal-user-emotion
git status --short
git log -3 --oneline
```

确认 HEAD 为 `99c6e3d` 且工作区仅有本交接文件后，从 Task 1 RED 开始。

## 已知风险与注意事项

1. `backend/services/voice_server.py` 已超过 85KB，不能继续堆积业务逻辑。新增声学、融合、策略、metrics 必须放到 `backend/services/emotion/`，主文件仅编排。
2. 现有 `voice_system_prompt.md` 仍含“单轮情绪立即启动香薰/音乐”和若干 `loop=true` 示例。Task 5 才修改；在 Task 4 前不要提前改提示词。
3. 当前 `dialogue.emotion` 表示助手 UI 表情，不是用户情绪。必须新增 `user_emotion`，并保持旧 `assistant_emotion` 事件兼容。
4. DeepSeek 是流式 JSON；`user_emotion` 必须排在 `dialogue` 前，才能在 TTS 文本开始流出前获得语义情绪。
5. 声学任务迟到后不能调用 `_parse_llm_json`、`_resolve_cjson` 或任何动作接口。
6. `emotion.enabled=false` 必须完整保留当前行为，不得因策略层改变旧 JSON 的动作语义。
7. 现有 `voice_server.py` 顶部 mock `audioop/pyaudioop`，测试导入时需保留该现状。
8. 计划里的代码片段是接口约束和起点；若官方 API 或现有代码要求小调整，应保持设计语义并补测试，不要扩大范围。

## 验证命令

后端全量测试：

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -v
```

Python 语法：

```powershell
backend\.venv\Scripts\python.exe -m compileall -q backend\services\emotion backend\services\voice_server.py backend\tools\replay_emotion.py
```

固件构建（只在 Task 8）：

```powershell
.\scripts\idf.ps1 build
```

不要在未获用户明确授权时 flash/monitor。

## Git 交付建议

- 所有开发继续在 `codex/multimodal-user-emotion`。
- 保持每个 Task 一个聚焦提交；审查修复可单独提交。
- 完成后先做整分支审查，再决定合并回 `main`。
- 当前 `main` 与功能分支都有新增基础提交；不要 reset 或覆盖用户历史。
