# Podcast Translation Skill 技术设计

本文档描述如何把当前播客翻译仓库改造成可供 little_claw 使用的 skill 后端，同时保留现有交互式 CLI 工作流。后续开发应以本文档作为实现依据。

## 目标

1. 新增一个稳定的非交互命令行入口 `podcast_tool`，大体遵循 `doc/podcast-translation-skill-contract.md` 的方向，但协议可以按本仓库的真实状态模型收敛，不需要逐字段照抄。
2. 支持 RSS 搜索、RSS episode 列表、异步翻译任务启动、状态查询、任务列表和取消。
3. 翻译长流程必须以后台“翻译任务”运行，`translate start` 在数秒内返回 `job_id`，不能让 little_claw 的 shell 调用阻塞到完整音频翻译结束。
4. 保留旧入口 `python main.py` 的人工菜单、确认、翻译后人工检查和断点续跑体验。
5. 在本仓库维护 skill 使用说明 `skills/podcast-translation-skill/SKILL.md`，但 skill 本身只提供指令，真正执行仍通过 `uv run podcast_tool ...`。

## 术语

本文档使用“翻译任务”指一次后台异步执行的播客翻译流程。它不是 little_claw 的 team task，也不是新的业务实体；本质上只是现有 `Pipeline.run()` 的一次后台运行记录。

翻译任务需要一个稳定句柄，供 `translate status`、`translate list`、`translate cancel` 使用。为了匹配 little_claw 协议，文档统一称为 `job_id`。这里的 job 只表示“后台翻译任务句柄”，不是 little_claw 的 team task，也不要求新增独立 job 数据库。

## 当前代码概况

现有仓库已经具备主要业务能力：

- `main.py`
  - 提供 `python main.py` 交互式入口。
  - 内置 `FEEDS` 播客列表。
  - 提供 `load_config()`、`create_providers()`、`parse_duration()`、`get_audio_url()` 等可复用逻辑。
  - 当前有两处交互阻塞：播客和 episode 选择、启动确认。
- `core/pipeline.py`
  - `Pipeline.run()` 串联下载、声纹提取、STT、翻译、TTS、Shownote。
  - `Pipeline._human_review_pause()` 在翻译后等待人工确认，这是 skill 后台运行时必须禁用的交互点。
  - Pipeline 内部大量 `print()` 适合人工 CLI，但不适合作为 JSON 命令输出。
- `core/progress.py`
  - 现有 SQLite 进度库按 episode 记录步骤结果，可继续用于 pipeline 断点续跑。
  - 它已经是本仓库的状态系统入口。skill 改造应优先演进这套 progress db，而不是并行新增一套状态库。
- `scripts/search_podcast_rss.py`
  - 已有 Apple Podcasts Search API 查询和 RSS 验证逻辑，可抽为 RSS service。
- `core/shownote_generator.py`
  - 已有 RSS entry 的 shownote 提取逻辑，可用于 `episodes list` 返回 `shownotes_original`。

## 总体架构

新增 skill 后端不替换旧 CLI，而是与旧 CLI 并行：

```text
python main.py
  -> 保持原交互式流程
  -> 使用 Pipeline(..., interactive_review=True)

uv run podcast_tool ...
  -> 新增非交互 JSON CLI
  -> RSS 命令同步返回 JSON
  -> translate start 写入 progress db 并启动后台 worker
  -> 后台 worker 使用 Pipeline(..., interactive_review=False)
  -> translate status/list/cancel 读取 progress db 返回 JSON

little_claw skill
  -> 只记录如何调用 podcast_tool
  -> 不直接承载长时间翻译进程
```

## 新增文件与职责

建议新增以下模块：

```text
podcast_tool/
  __init__.py
  cli.py                 # argparse 入口，所有 stdout 只输出单个 JSON object
  jsonio.py              # JSON response、错误格式、退出码工具
  rss.py                 # Apple 搜索、RSS 解析、episode 规范化
  state.py               # 对 core.progress.ProgressTracker 的 skill 状态封装
  runner.py              # 创建 provider、运行 Pipeline、更新翻译任务状态
  worker.py              # 后台进程入口: python -m podcast_tool.worker run --job-id ...
  process.py             # 启动/取消后台进程，pid 检查

core/
  app_factory.py         # 可选：从 main.py 抽出 load_config/create_providers
```

`main.py` 应尽量少改。可把 `load_config()` 和 `create_providers()` 抽到 `core/app_factory.py`，然后 `main.py` 和 `podcast_tool.runner` 共同复用；也可以第一版先从 `main.py` import 这两个函数，但长期不推荐让非交互入口依赖交互式入口文件。

`pyproject.toml` 需要增加 console script：

```toml
[project.scripts]
podcast-translator = "main:main"
podcast_tool = "podcast_tool.cli:main"
```

## Pipeline 兼容改造

为保留旧行为并支持后台非交互运行，`Pipeline` 增加可选参数：

```python
Pipeline(
    config,
    stt,
    llm,
    tts,
    storage,
    progress=progress,
    shownote_llm=shownote_llm,
    interactive_review=True,
    status_callback=None,
)
```

含义：

- `interactive_review=True` 是默认值，旧 `python main.py` 不传此参数，行为不变。
- `interactive_review=False` 时跳过 `_human_review_pause()`，不能调用 `input()`。
- `status_callback` 是可选回调，签名建议为 `callback(stage: str, progress: float, message: str, artifacts: dict | None = None)`。
- `_run_step()` 在每个步骤开始和完成时调用 `status_callback`，便于后台翻译任务持久化进度。

进度映射应基于真实耗时权重，而不是平均分段。按一次已记录运行日志：

```text
download: 15.4s
voiceprint: 1635.6s
stt: 173.4s
translate: 664.4s
tts: 3433.8s
shownote: 21.5s
total: 5944.1s
```

折算后，TTS 约占 57.8%，voiceprint 约占 27.5%，translate 约占 11.2%，STT 约占 2.9%，download 和 shownote 都小于 1%。第一版使用以下默认映射，后续可以把权重放到配置中按机器和 provider 调整：

| stage | 开始进度 | 完成进度 |
| --- | ---: | ---: |
| queued | 0.00 | 0.00 |
| download | 0.00 | 0.01 |
| voiceprint | 0.01 | 0.29 |
| stt | 0.29 | 0.32 |
| translate | 0.32 | 0.43 |
| tts | 0.43 | 0.99 |
| shownotes | 0.99 | 1.00 |
| done | 1.00 | 1.00 |

如果跳过某些步骤，进度条不要保留空洞。实现时应根据实际 `skip_steps` 重新归一化剩余步骤权重。例如 `--skip-tts` 后，`voiceprint` 和 `translate` 的可见进度会占更大比例。

Pipeline 的 `print()` 暂时不需要全部替换。后台 worker 必须把 stdout/stderr 重定向到翻译任务的 `run.log`，保证 `podcast_tool translate start/status/list/cancel` 的 stdout 仍是纯 JSON。

## 统一状态系统

不新增独立任务数据库。skill 后端使用并演进现有 `ProgressTracker`，默认仍写入当前 progress db：

```text
./data/progress.db
```

可以通过配置覆盖：

```yaml
output:
  progress_db: "./data/progress.db"
  tasks_dir: "./output/translation_tasks"
```

### 表结构演进

现有表：

```sql
episodes (
    episode_id TEXT PRIMARY KEY,
    audio_url TEXT NOT NULL UNIQUE,
    podcast_name TEXT DEFAULT '',
    episode_title TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    error_message TEXT,
    created_at TEXT,
    updated_at TEXT
);

step_results (
    episode_id TEXT NOT NULL,
    step_name TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    result_data TEXT,
    error_message TEXT,
    completed_at TEXT,
    PRIMARY KEY (episode_id, step_name)
);
```

第一版不要增加独立 `translation_tasks` 或事件表。直接扩展 `episodes`，让一个 episode 记录同时承担人工 CLI 的断点续跑和 skill 后台翻译任务状态：

```sql
ALTER TABLE episodes ADD COLUMN job_id TEXT;
ALTER TABLE episodes ADD COLUMN rss_url TEXT DEFAULT '';
ALTER TABLE episodes ADD COLUMN rss_episode_id TEXT DEFAULT '';
ALTER TABLE episodes ADD COLUMN page_url TEXT DEFAULT '';
ALTER TABLE episodes ADD COLUMN published_at TEXT DEFAULT '';
ALTER TABLE episodes ADD COLUMN stage TEXT DEFAULT 'pending';
ALTER TABLE episodes ADD COLUMN progress REAL DEFAULT 0;
ALTER TABLE episodes ADD COLUMN message TEXT DEFAULT '';
ALTER TABLE episodes ADD COLUMN estimated_minutes INTEGER;
ALTER TABLE episodes ADD COLUMN pid INTEGER;
ALTER TABLE episodes ADD COLUMN started_at TEXT;
ALTER TABLE episodes ADD COLUMN finished_at TEXT;
ALTER TABLE episodes ADD COLUMN target_lang TEXT DEFAULT 'zh-CN';
ALTER TABLE episodes ADD COLUMN voice_clone INTEGER DEFAULT 1;
ALTER TABLE episodes ADD COLUMN skip_steps_json TEXT DEFAULT '[]';
ALTER TABLE episodes ADD COLUMN work_dir TEXT DEFAULT '';
ALTER TABLE episodes ADD COLUMN log_path TEXT DEFAULT '';
ALTER TABLE episodes ADD COLUMN artifacts_json TEXT DEFAULT '{}';
ALTER TABLE episodes ADD COLUMN error_json TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_episodes_job_id
ON episodes(job_id)
WHERE job_id IS NOT NULL AND job_id != '';

CREATE INDEX IF NOT EXISTS idx_episodes_status
ON episodes(status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_episodes_rss_episode_active
ON episodes(rss_url, rss_episode_id)
WHERE status IN ('queued', 'running')
  AND rss_url != ''
  AND rss_episode_id != '';
```

字段说明：

- `job_id`: 对外唯一句柄，格式建议 `podcast_YYYYMMDD_HHMMSS_<hash6>`。这是 little_claw 协议字段，内部也直接使用这个名字。
- `episode_id`: 继续作为内部主键。直接音频 URL 仍按现有 SHA256 生成；RSS 启动时可以使用 `rss_url + rss_episode_id` 生成，保证同一 RSS episode 幂等。
- `work_dir`: 翻译任务专属目录，如 `./output/translation_tasks/podcast_20260503_9f3a2c`。
- `log_path`: 后台 worker 日志文件。
- `artifacts_json`: `work_dir`、`audio_zh`、`shownotes_zh`、`transcript_en`、`transcript_zh` 等产物路径。
- `error_json`: `{code, message, retryable}`。
- `step_results`: 继续记录每一步的详细结果。它已经能承载阶段历史和断点续跑，不再新建事件表。

如果实际开发时发现 `episodes` 扩字段让语义过于混乱，再升级为新的 `WorkflowStateStore` 替换 `ProgressTracker`；但仍应保持“一套状态系统”，不要让旧 progress db 和新任务数据库并行存在。

### 状态值

对外 JSON 仍使用 contract 的大方向，但不要求一字不差复刻字段。状态值建议保持以下集合，便于 little_claw 判断：

- `queued`
- `running`
- `completed`
- `failed`
- `cancelled`

取消语义：

- `translate cancel` 设置 `status=cancelled`、`stage=cancelled`。
- 如果 `pid` 仍存活，发送 SIGTERM。
- 后台 worker 在步骤切换前检查翻译任务是否已取消；第一版如无法中断 provider 内部长轮询，允许等当前外部调用返回后退出。
- 对已经 `completed`、`failed`、`cancelled` 的翻译任务，再 cancel 应幂等返回当前状态。

## CLI 协议实现

所有新命令都通过 `podcast_tool.cli:main` 实现。除 `--help` 外，命令必须支持 `--json`，且 stdout 输出单个 JSON object。JSON 字段以 little_claw 实际需要为准：能发现 RSS、列 episode、启动翻译任务、拿到 `job_id`、查询状态、定位产物和错误即可。

错误输出规范：

```json
{
  "ok": false,
  "translation": null,
  "error": {
    "code": "INVALID_ARGUMENT",
    "message": "Missing --rss-url or --audio-url.",
    "retryable": false
  }
}
```

建议退出码：

| 退出码 | 含义 |
| ---: | --- |
| 0 | 成功，包含业务上的空结果 |
| 2 | 参数错误或 JSON 协议错误 |
| 3 | RSS、episode、翻译任务等资源不存在 |
| 4 | 配置或认证错误 |
| 5 | 后台任务已失败 |
| 6 | 外部 provider 临时错误 |
| 10 | 未分类运行时错误 |

### `rss find`

命令：

```bash
uv run podcast_tool rss find --query "Acquired podcast" --json
```

实现：

- 从 `scripts/search_podcast_rss.py` 抽出 `search_apple_podcasts()` 和 `verify_rss()` 到 `podcast_tool/rss.py`。
- Apple Search API 结果按以下规则转换：
  - `title`: `collectionName`
  - `publisher`: `artistName`
  - `rss_url`: `feedUrl`
  - `website_url`: `collectionViewUrl`
  - `description`: Apple API 没有时先返回空字符串；可选用 RSS feed 的 subtitle/description 补充。
  - `language`: Apple 返回缺失时从 RSS feed 取 `language`，仍缺失返回空字符串。
  - `confidence`: 基于标题完全匹配、标题包含、publisher 匹配、RSS 可验证和活跃度计算 0 到 1。
- 没结果返回 `ok: true, feeds: []`，不算错误。

### `episodes list`

命令：

```bash
uv run podcast_tool episodes list --rss-url "https://example.com/feed.xml" --limit 10 --json
```

实现：

- 使用 `requests.get()` 下载 RSS，尊重 `config.yaml` 的 `rss.timeout` 和 `rss.proxy`。
- 使用 `feedparser.parse()` 解析。
- `episode_id` 生成规则：
  1. 优先 `entry.id` 或 `entry.guid`。
  2. 其次 audio URL。
  3. 再次 page URL。
  4. 最后 `title + published`。
  5. 对过长或不安全字符串统一输出 `sha256(...)[0:16]`，但要保持稳定。
- `audio_url` 复用 `main.py` 中的 `get_audio_url()` 逻辑。
- `duration_seconds` 复用 `parse_duration()`。
- `published_at` 使用 `email.utils.parsedate_to_datetime()` 转成 UTC ISO 8601，无法解析时返回空字符串或原始短文本。
- `shownotes_original` 调用 `extract_shownote_from_entry(entry)["description"]`，限制长度，建议最多 4000 字符。
- `shownotes_zh` 第一版不在该命令生成，返回空字符串，避免把快速列表命令变成 LLM 调用。

### `translate start`

命令：

```bash
uv run podcast_tool translate start \
  --rss-url "https://example.com/feed.xml" \
  --episode-id "stable-id-or-guid" \
  --target-lang "zh-CN" \
  --voice-clone true \
  --json
```

也支持：

```bash
uv run podcast_tool translate start \
  --audio-url "https://example.com/audio.mp3" \
  --title "Episode title" \
  --page-url "https://example.com/episode" \
  --target-lang "zh-CN" \
  --voice-clone true \
  --json
```

实现流程：

1. 解析参数并加载配置。
2. 如果传了 `--rss-url --episode-id`，调用 RSS service 定位 episode，并补全 title、audio_url、page_url、published_at、shownotes_original。
3. 如果直接传 `--audio-url`，使用命令行参数构造 episode。
4. 计算内部 `episode_id`。RSS 启动时使用 `rss_url + episode_id` 的 hash；直接音频启动时使用 `audio_url` 的 hash。
5. 如果存在同一 episode 的 active 翻译任务且未传 `--force`，直接返回已有翻译任务。
6. 在 `episodes` 中创建或更新翻译任务记录，状态为 `queued`。
7. 通过 `subprocess.Popen()` 启动后台 worker：

```bash
python -m podcast_tool.worker run --job-id <job_id> --config <config_path>
```

8. Popen 时设置：
   - `cwd` 为仓库根目录。
   - stdout/stderr 写入 `log_path`。
   - Linux 下优先使用 `start_new_session=True`，保证 shell 退出后 worker 继续运行。
9. 更新翻译任务的 `pid`。
10. 立即返回翻译任务 JSON。

`--voice-clone false` 应转换为 `skip_steps=["voiceprint"]`，TTS 仍可使用默认音色。若用户显式不需要 TTS，可额外支持 `--skip-tts`，但 contract 的主路径应默认生成中文音频。

### 后台 worker

`podcast_tool.worker` 只负责执行一个翻译任务：

1. 从 progress db 读取翻译任务。
2. 如果翻译任务已取消，直接退出。
3. 设置 `status=running, stage=download`。
4. 创建 `ProgressTracker`，复用同一个 progress db；不要再打开第二个状态数据库。
5. 创建 providers 和 shownote LLM。
6. 调用 `Pipeline.run(..., interactive_review=False, status_callback=...)`。
7. 将 `PipelineContext` 转成 artifacts：
   - `audio_zh`: `ctx.final_audio_path`
   - `shownotes_zh`: `ctx.shownote_path`
   - `transcript_en`: `ctx.transcript_path`
   - `transcript_zh`: `ctx.translation_path`
   - `work_dir`: 翻译任务工作目录
   - `log`: log path
8. 成功时设置 `status=completed, stage=done, progress=1`。
9. 异常时设置 `status=failed, stage=<当前阶段>, error_json=...`，退出码返回非 0。

错误分类建议：

- 配置文件缺失或 provider key 缺失：`CONFIG_ERROR`, retryable=false。
- RSS 或音频 URL 不可访问：`RESOURCE_NOT_FOUND` 或 `DOWNLOAD_ERROR`, retryable=true。
- STT provider 报错：`STT_PROVIDER_ERROR`, retryable=true。
- LLM provider 报错：`LLM_PROVIDER_ERROR`, retryable=true。
- TTS provider 报错：`TTS_PROVIDER_ERROR`, retryable=true。
- 本地依赖缺失，如 ffmpeg：`LOCAL_DEPENDENCY_ERROR`, retryable=false。
- 取消：不写 error，状态为 `cancelled`。

### `translate status`

命令：

```bash
uv run podcast_tool translate status --job-id "podcast_20260503_9f3a2c" --json
```

实现：

- 只读取 progress db，不启动任何长流程。
- 如果翻译任务不存在，返回退出码 3。
- 如果 `status=running` 但 `pid` 不存在，且没有正常完成标记，应将翻译任务标记为 `failed`，错误为 `WORKER_LOST`。
- 返回面向 little_claw 的完整翻译任务对象。字段可以比 contract 精简，但必须包含 `job_id`、`status`、`stage`、`progress`、`episode`、`artifacts`、`error`。

### `translate list`

命令：

```bash
uv run podcast_tool translate list --status active --json
```

实现：

- `--status active` 等价于 `queued,running`。
- 也支持单状态过滤：`queued`、`running`、`completed`、`failed`、`cancelled`。
- 输出简化翻译任务对象，字段名以 `job_id`、`status`、`stage`、`progress`、`episode` 为主。

### `translate cancel`

命令：

```bash
uv run podcast_tool translate cancel --job-id "podcast_20260503_9f3a2c" --json
```

实现：

- 更新翻译任务状态为 `cancelled`。
- 如果 `pid` 存活，发送 SIGTERM。
- 返回更新后的翻译任务 JSON。
- 已终态翻译任务返回当前终态，退出码 0。

## JSON 输出隔离

为了保证 `podcast_tool` stdout 只有 JSON，需要遵守：

1. `cli.py` 负责捕获所有异常，并统一调用 `jsonio.write_response()`。
2. RSS 命令内部不使用 `print()`。
3. 后台 worker 可以写日志，但 `translate start` 本身不能让 Pipeline 在当前进程执行。
4. `translate status/list/cancel` 不 import 会触发 provider 初始化或 provider 打印的模块。
5. 如果有第三方库向 stdout 打印，CLI 同步命令中用 `contextlib.redirect_stdout(sys.stderr)` 包裹内部调用，最终 JSON 仍写 stdout。

## 配置策略

第一版继续使用现有 `config.yaml`。新增配置项必须有默认值：

```yaml
output:
  progress_db: "./data/progress.db"
  tasks_dir: "./output/translation_tasks"

podcast_tool:
  estimated_minutes: 60
  rss_shownote_max_chars: 4000
  worker_poll_cancel_seconds: 5
```

skill 文档里命令统一写：

```bash
cd /home/zhanghuibin02/code/podcast_translation && uv run podcast_tool ... --json
```

后续如果迁移到其他机器，只需要更新 `SKILL.md` 中的仓库路径和配置路径。

## Skill 目录

新增项目级 skill 文件。它本质上是 little_claw 读取的一份使用说明书，源文件放在本仓库可被 git 追踪的位置：

```text
skills/podcast-translation-skill/SKILL.md
```

`SKILL.md` 内容按 contract 的推荐模板编写，但要写死真实命令 `uv run podcast_tool` 和仓库绝对路径。它不需要生成代码，也不参与 Python 包发布。部署给 little_claw 时，再把这个文件复制或软链到 little_claw 实际扫描的目录，例如 `.little_claw/skills/podcast-translation-skill/SKILL.md` 或 `~/.little_claw/skills/podcast-translation-skill/SKILL.md`。skill 中必须强调：

- 所有命令都带 `--json`。
- 启动翻译前必须获得用户明确审批。
- 启动后只记录 `job_id`，不能等待完整翻译。
- 状态巡检只汇报 completed、failed、cancelled，queued/running 静默更新项目状态。

## 兼容性边界

必须保持不变：

- `python main.py` 可以继续人工选择分类、播客、episode。
- `python main.py --url ...`、`--local-file ...`、`--skip-tts`、`--skip-voiceprint`、`--skip-shownote`、`--no-resume` 继续可用。
- 旧 CLI 默认仍有启动确认。
- 旧 CLI 默认仍在翻译后暂停等待人工检查。
- 现有 `ProgressTracker` 断点续跑能力继续可用。

允许新增但不能破坏：

- Pipeline 新增参数必须有默认值。
- 抽取配置和 provider 工厂时，`main.py` 的外部行为不变。
- 状态系统只能有一套。优先扩展现有 `ProgressTracker` 和 `progress.db`；如果后续证明旧表结构不适合，再整体替换为新的状态实现，而不是让两套状态并存。

## 实施步骤

建议按以下顺序开发，每步都应保持旧 CLI 可运行：

1. 抽取 `core/app_factory.py`，让 `main.py` 继续通过新模块加载配置和创建 providers。
2. 给 `Pipeline` 增加 `interactive_review` 和 `status_callback`，默认保持旧行为。
3. 新增 `podcast_tool/jsonio.py` 和 `podcast_tool/cli.py` 空框架，接入 `pyproject.toml` console script。
4. 新增 `podcast_tool/rss.py`，实现 `rss find` 和 `episodes list`。
5. 新增 `podcast_tool/state.py`，封装 `ProgressTracker` 的 skill 状态读写、翻译任务 JSON 序列化和幂等 active 任务查询。
6. 新增 `podcast_tool/worker.py` 和 `podcast_tool/runner.py`，让后台 worker 能跑完整 Pipeline。
7. 实现 `translate start/status/list/cancel`。
8. 新增 `skills/podcast-translation-skill/SKILL.md`，作为仓库内维护的 skill 说明书。
9. 补充测试和手工验收。

## 测试计划

### 单元测试

新增测试建议：

- `test_podcast_tool_rss.py`
  - episode id 稳定生成。
  - duration 解析。
  - published_at ISO 转换。
  - shownotes_original 清洗和长度限制。
- `test_podcast_tool_state.py`
  - 创建翻译任务。
  - 同 episode active 翻译任务幂等返回。
  - status active 过滤。
  - cancel 幂等。
  - 翻译任务 JSON 包含 little_claw 所需核心字段。
- `test_podcast_tool_jsonio.py`
  - 成功和错误响应都是单个 JSON object。
  - 退出码映射正确。

### 手工验收

不依赖真实 provider 的快速验收：

```bash
uv run podcast_tool rss find --query "Acquired podcast" --json
uv run podcast_tool episodes list --rss-url "https://acquired.libsyn.com/rss" --limit 3 --json
uv run podcast_tool translate list --status active --json
python main.py --help
```

依赖真实 provider 的端到端验收：

```bash
uv run podcast_tool translate start \
  --audio-url "https://example.com/audio.mp3" \
  --title "Episode title" \
  --target-lang "zh-CN" \
  --voice-clone true \
  --json

uv run podcast_tool translate status --job-id "<job_id>" --json
uv run podcast_tool translate list --status active --json
```

旧 CLI 回归：

```bash
python main.py --local-file /path/to/small.mp3 --name "podcast" --title "smoke-test" --skip-tts --skip-shownote
```

验收标准：

- `rss find`、`episodes list` 输出可被 `json.loads()` 解析。
- `translate start` 在 10 秒内返回。
- shell 进程退出后 worker 仍继续运行。
- `translate status` 在 Python 进程重启后仍能查询。
- 完成翻译任务包含绝对 artifacts 路径。
- 失败翻译任务包含 `error.code`、`error.message`、`retryable`。
- 旧 `python main.py` 的交互流程未被移除。

## 开发风险与处理

- Pipeline 内部和 provider 内部 `print()` 很多。第一版用后台日志重定向解决，不做大规模 logging 重构。
- Provider 的长轮询调用可能不能即时取消。第一版采用翻译任务状态取消加进程 SIGTERM；后续再细化 provider 层取消。
- RSS episode 的 `entry` 需要传入 Pipeline 才能生成 shownote。`translate start` 从 `--rss-url --episode-id` 启动时应把原始 entry 序列化到翻译任务记录或 worker 里重新拉取。
- 直接 `--audio-url` 启动时没有 RSS entry，shownote 只能基于 transcript 和 translation 生成，`shownotes_original` 为空。
- `pyproject.toml` 当前项目名是 `podcast-translator`，新命令名按 contract 使用 `podcast_tool`，两者可以并存。
