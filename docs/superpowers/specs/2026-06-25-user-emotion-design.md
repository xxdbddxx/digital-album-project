# 多模态用户语音情绪增强设计

## 目标与边界

在现有稳定链路 `INMP441 → WakeNet → VAD → WebSocket → faster-whisper → DeepSeek → CosyVoice → MAX98357A` 上增加旁路情绪增强，不重写主链路。关闭功能开关后必须恢复当前行为，无需回滚固件或数据库。

非必要不改 WakeNet、VAD、端点检测、AEC、持久 WebSocket、Whisper CUDA、DeepSeek 流式回复、先显示文本再生成 TTS、CosyVoice、照片/音乐语义路由、唤醒打断和连续对话状态。

允许的最小接入点：并行启动声学分析、向现有 DeepSeek turn 注入结构化情绪上下文、区分用户情绪与助手表达、继续兼容 `assistant_emotion` 事件、将隐式硬件动作移到确定性策略门控。

不做医疗诊断、跨会话情绪画像、ASR/LLM/TTS 替换、默认 60% 音量调整、情绪音乐循环或情绪覆盖明确指令。

## 架构与时序

```text
PCM utterance
├─ faster-whisper → ASR 文本 → 现有 DeepSeek turn 内的语义情绪专家
├─ 云端声学情绪专家
└─ 现有录音/调试路径
                                  ↓
                         本地确定性融合器
                                  ↓
               回复风格 / UI / TTS / 硬件策略
```

声学请求在 PCM 可用时启动。ASR 完成后开始 400ms 软截止：声学结果已到则参与当前回复规划，否则立即走现有文本链路，不继续阻塞首句。

迟到结果只允许更新 UI 和当前会话状态，不得撤回已播语音、修改已执行明确指令，或单独触发音乐、香薰和照片。声学服务超时、失败、格式错误或整体不可用时，现有 ASR→DeepSeek→TTS 继续运行。

## 情绪模型

用户情绪 8 类：`joy`、`sadness`、`anxiety`、`anger`、`fatigue`、`loneliness`、`calm`、`neutral`。

专家统一输出：

```json
{
  "label": "anxiety",
  "valence": -0.65,
  "arousal": 0.82,
  "intensity": 0.76,
  "confidence": 0.81,
  "evidence": ["语速偏快", "停顿较短"],
  "source": "acoustic"
}
```

`valence` 范围 -1~1，其余数值范围 0~1。非法输出归一为低置信中性。

助手表达独立为：`cheerful`、`empathic`、`soothing`、`calm`、`gentle`、`warm`、`neutral`。现有设备侧 emotion 继续表示助手 UI/表达，不作为用户情绪。

## 确定性融合

初始可配置权重：

- `loneliness`、`sadness`：文本 0.70 / 声学 0.30；
- `anger`、`fatigue`、`anxiety`：文本 0.45 / 声学 0.55；
- `joy`、`calm`、`neutral`：文本 0.50 / 声学 0.50。

双专家同类时提高置信度。相近类别保留分布并融合连续维度。明显冲突设置 `mixed=true`，降低授权置信度，采用克制表达并禁止自动硬件动作。一个专家为 neutral 只表示缺少证据，不覆盖另一个高置信结果。

只有一个有效专家时可影响 UI 和回复/TTS，不得授权隐式硬件动作。不再增加第三次 LLM 融合调用。

## 会话状态与阈值

仅在当前会话内对最近 3~5 轮的类别分布、valence、arousal、intensity 做指数平滑，会话结束即清空。强当前证据应快速反转状态；连续反转时降低历史权重。明确控制指令始终绕过情绪历史。

- 置信度 <0.45：中性默认处理；
- 0.45~0.65：只更新 UI；
- 0.65~0.80：调整回复和 TTS；
- ≥0.80 且双专家一致，或连续两轮一致：隐式环境动作获得候选资格。

候选资格不等于执行，仍需硬件策略校验。

## 回复、TTS、UI 与硬件

| 用户状态 | 助手风格 | 行为 |
| --- | --- | --- |
| joy | cheerful | 简短积极、同频庆祝 |
| sadness/loneliness | empathic | 接纳、温和，不强行乐观 |
| anxiety | soothing | 稳定、短句、低认知负担 |
| anger | calm | 不对抗、不镜像愤怒 |
| fatigue | gentle | 简短、低强度 |
| calm | warm | 自然平稳 |
| neutral | neutral | 当前默认 |

首版可在窄范围动态调整 CosyVoice speech rate，但保持默认语音音量 60%。指令式情感 TTS 使用独立能力开关，失败回退当前固定设置。

UI 不展示“你现在很焦虑”等诊断式结论。低置信或 mixed 使用 neutral/warm。保留现有 `assistant_emotion` 兼容，新增结构化遥测必须可被旧固件忽略。

明确指令继续由现有语义路由执行。情绪只能改变回复表达，不得改变照片、方向、歌曲、歌手、启停、用户指定香薰通道或音量。

隐式情绪动作只能是候选，不能直接执行 LLM 输出。普通模式要求双专家高置信一致并获得下一轮确认，或用户明确同意。比赛 `demo_mode=true` 时，双专家一致且融合置信度 ≥0.80 可自动执行一次。

两种模式都要求：无模态冲突、不是迟到声学单独触发、不与明确指令冲突、当前会话未执行同类干预、冷却未生效、媒体目标确定有效。

情绪音乐保持非循环，默认音量保持 60%。照片策略：悲伤/孤独只使用高置信个人回忆；喜悦可用庆祝或家庭照片；焦虑/愤怒不自动切图；疲惫可用低唤醒自然照片；无匹配时不得随机兜底。

## 配置

```text
emotion.enabled
emotion.acoustic_provider
emotion.acoustic_timeout_ms = 400
emotion.demo_mode = true
emotion.session_window_turns
emotion.ui_threshold
emotion.response_threshold
emotion.action_threshold
emotion.intervention_cooldown_sec
emotion.tts_style_enabled
```

服务凭证只存 `backend/.env.local`，不得打印、提交或发给 ESP32。

## 故障处理

- 声学超时/失败：仅文本回复，禁止隐式硬件；
- Whisper 失败：要求重说，不用声学情绪猜意图；
- 专家冲突：克制回复，禁止隐式硬件；
- 非法 schema：低置信 neutral；
- 迟到结果：只更新会话/UI；
- 情绪子系统异常：在边界捕获并继续现有链路。

## 可观测性

逐轮记录 turn ID、ASR/声学耗时、软截止命中、两个专家结果、融合结果与原因、助手风格、动作批准/拒绝原因、首个 LLM token、首个显示文本、首个 TTS 包和总延迟。不得打印密钥，也不得新增不受控的第二份音频归档。

## 验收

建立中文可回放测试集，每类至少 20 条，覆盖自然说话、表演式语气、歧义、模态冲突、带情绪的明确指令、噪声和弱信号。

目标：

- macro F1 ≥0.72；
- anxiety、sadness、fatigue 召回率 ≥0.75；
- 高置信错误率 ≤5%；
- 隐式硬件误触发率 ≤3%；
- 明确指令准确率不低于当前基线；
- 比赛网络下声学软截止命中率 ≥80%；
- 首句延迟增量 P50 ≤200ms、P95 ≤450ms；
- 声学服务完全断开时，当前语音体验仍可用。

测试分层：schema/融合/阈值/平滑/冲突/迟到/冷却/明确指令优先的单元测试；Whisper、声学、DeepSeek、TTS 不同完成顺序的集成测试；固定 WAV 回放；ESP32 事件顺序、TTS 不断流、UI、唤醒打断、连续对话实机测试；照片搜索、方向、音乐元数据、明确歌曲不随机兜底、音乐非循环回归测试。

比赛演示覆盖 joy 自动一次干预、anxiety、fatigue、模态冲突无动作、云端超时正常降级。

## 实现边界

优先新增独立小模块：声学 provider adapter、情绪 schema/校验、确定性融合与会话状态、回复风格映射、硬件授权策略、指标钩子。

`voice_server.py` 只在现有边界编排这些模块。除非测试证明必要，否则不修改已有函数。每个实施任务都必须包含相关回归验证。
