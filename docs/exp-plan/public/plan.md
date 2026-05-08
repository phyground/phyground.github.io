# Public 网站 — 视频 + Leaderboard

> 给外部观众（论文读者 / reviewer / 招生）看的公开站点：
> 1. **Video Gallery** — 浏览各视频生成模型的输出，按物理定律对比
> 2. **Leaderboard** — 各 video model 在 wmbench 上的评测排名

参考实现：`vm-web/`（Flask + Nginx + Let's Encrypt，极简衬体设计），
内网已有的私有可视化：`evals/vis_eval_server.py`（见 `docs/exec-plan/vis/vis.md`）。

本站点 = 把内网 vis 工具里"对外可分享"的那一面，按 vm-web 的部署/视觉模板做成正式公开站。

---

## 1. 目标 & 非目标

**做：**
- 公开站，无需登录，可被搜索引擎索引
- Video Gallery：按物理定律 / 按模型两个维度浏览，可同 prompt 跨模型对比
- Leaderboard：按 evaluator × dataset × subset 切片展示排名，可下载原始 JSON
- 论文配套：每条记录有可固定 URL（permalink），方便正文引用

**不做（v1）：**
- 不做人工标注 / 不收集用户反馈 — 那是 `evals/human_eval/`
- 不做生成器交互页（vm-web 的 Data Engines 风格） — wmbench 没有 generator 仓库矩阵
- 不做账户系统、评论、上传

---

## 2. 数据来源（不抄进站点，直接引用）

| 内容 | 源 | 备注 |
|------|-----|------|
| 视频模型清单 | `videogen/runner/MODEL_CATALOG.py` + `docs/exp-plan/videogen/models.md` | 含 catalog key / 参数量 / fps / 分辨率 |
| 生成视频 | `data/videos/<model>-<dataset>/*.mp4` | 各模型已落盘，命名见各 dataset prompts json |
| Prompt + first_frame | `data/prompts/<dataset>/*.json` | 与视频通过 stem 对齐，详见 `evals/vis_eval_server.py` |
| 评测分数（master 索引） | `evals/eval_registry.json` | 唯一权威入口；查 schema/evaluator/video_model/dataset → source_json |
| 物理定律精选样例 | `data/paperdemo/manifest.csv` + `data/paperdemo/<law>/*.mp4` | 已按 law 分组的展示集，每行 `law, video_id, n_ann, dataset(=model), src_filename, dst_path` |
| 物理定律图 | `data/paperdemo/figs/<law>.pdf` | 论文 figure，可直接转 svg/png 上首页 |
| 数据集别名 / 评分 JSON 路径 | `data/vis_datasets.json` | 内网 viewer 已用，直接复用 schema |
| 评分 schema | `evals/eval_types.py` + `docs/exp-plan/evaluator/scoring-spec.md` | 字段定义、版本号 |

**原则：站点持有完整快照，源被删也能独立存活。**

- builder 跑一次 = 把上述源全部 **冷拷贝** 到 `public/snapshot/`，站点运行时**只读 snapshot**，从不回查 `data/` 或 `evals/`
- snapshot 自带 `MANIFEST.json`（git sha + 时间戳 + 每个文件 sha256），任何时间能验证完整性
- 与 wmbench 主库解耦：snapshot 目录独立 rsync 到部署机磁盘 + 离线备份（外置盘 / S3 / 第二台 VM），按"冷热双份"维护
- 大文件（视频 / 图）**不进 git**（已被 `.gitignore: *.mp4 / *.jpg / data/videos/` 覆盖）；durability 来自**多副本存储**而非版本控制
- 小文件（JSON manifest / scoring）**进 git**，需在 `public/.gitignore` 白名单（`!snapshot/index/*.json`），保证元数据可追溯

---

## 3. 页面设计

### `/` Home
- Hero：1-2 句项目说明 + 论文链接 + GitHub 链接
- Headline 数字（动态来自 `site_config.json`）：模型数、prompt 数、人工标注数、评测组合数
- "Featured comparison"：从 `paperdemo/manifest.csv` 取一个 law（如 collision），横排展示 3-4 个模型 + 真实视频（autoplay muted loop）
- 三个入口卡片：Leaderboard / Video Gallery / Paper

### `/leaderboard/`
- **筛选条件**（query params 控制，URL 可分享）：
  - `evaluator` ∈ {qwen-9b, qwen-27b, qwen-397b, claude-opus-4.7, gemini, gpt, ourckpt, ...}（来自 registry）
  - `dataset` ∈ {humaneval, wmb, video_phy_2, physics_iq, openvid}
  - `subset`（如 humaneval_set / hard_subset）
  - `schema` ∈ {plain/v1, plain/v2, subq_hint/v1, ...}
  - `fps`、`prompt_mode`
- **表格**：行 = video model，列 = `gen_avg`、`phys_avg`、各 metric（参 scoring-spec）。可按列排序。
- **每行折叠展开**：显示 per-metric 分数 / 样本数 / coverage / 评测时间 / `notes`
- "Download raw JSON" 按钮 → 直接 serve `source_json`
- "View videos" 按钮 → 跳 `/videos/?model=<m>&dataset=<d>`
- 多组结果同 (model, dataset) 时取最新（按 `datetime`），并在 tooltip 列出历史
- Caveat 注脚：domain-pooled vs macro-averaged（参 `f006b3c` commit message）

### `/videos/` Video Gallery
两个浏览模式：

**A. By Law（默认）** — 来自 `paperdemo/manifest.csv`
- 左侧导航：13 类物理定律（含 figs PDF 缩略图）
- 中间：所选 law 下所有 (model, video) 横向网格，autoplay loop muted
- 每个视频卡片底部：model 名 + 来源 dataset + n_ann + 原 src_filename
- 点开 modal：放大视频 + prompt 全文 + first_frame + 各 evaluator 给的分数（拉取 `eval_registry`）

**B. By Model**
- 左侧导航：MODEL_CATALOG 列出的 11+ 模型 + Veo 3.1
- 中间：所选 model × dataset 网格 (humaneval / wmb / physics_iq / video_phy_2)
- 同 prompt 跨 dataset 视频（如某 prompt 同时在 humaneval 和 wmb）：合并为一卡

**C. Same-prompt comparison（modal/锚点页）**
- URL：`/videos/compare?prompt_id=<id>`
- 一行：input first_frame + prompt
- 下方网格：所有跑过这个 prompt 的模型并排 + 真实视频（如 openvid 对应片段）
- 与 vis_eval_server 的"Video 下拉框 + Sort"对应，但只读

### `/models/<key>/`
- 每个 video model 的详情页：参数 / fps / 分辨率（从 `models.md` 表格抽）+ 在各 leaderboard 切片中的排名汇总 + 代表视频选 6-9 条（按 paperdemo 优先 → fallback 随机）

### `/about/`
- 团队、引用 bib、license、contact
- Method overview：跳 `paper/` PDF

---

## 4. 技术栈

完全照搬 vm-web 模板，避免重复造轮子：

| 层 | 选型 | 理由 |
|----|------|------|
| Web 框架 | Flask 3 + Jinja2 | 与 vm-web 一致，路由 blueprint 化 |
| 前端 | 静态 HTML/CSS + 轻量 vanilla JS | 无 SPA，搜索引擎友好。vm-web 的 `static/css/base.css` 直接复用 |
| 视频服务 | Flask `send_from_directory` + `safe_join`，参考 `vm-web/app.py: serve_video` | path traversal 防护已写好 |
| 转码缓存 | ffmpeg → `assets/cache/<hash>.mp4`，与 vm-web 一致 | 论文展示用的 paperdemo 已是 mp4，无需转 |
| 数据装载 | 启动时一次性读 `snapshot/index/site_config.json` 编入内存索引；从不回查源 | snapshot 是站点唯一真相 |
| 反向代理 | Nginx + Let's Encrypt（certbot） | 与 vm-web README §"Server Setup (Fresh Machine)" 同 |
| 进程 | gunicorn 4 workers | vm-web 已验证 |

**视觉规范** — 与 vm-web 一致（论文气质统一）：
- Times New Roman 衬体
- 黑白灰，1px 矩形边框
- 不放 emoji / 卡通图

> 不照搬的：vm-web 有 150 个 generator 子模块、Lambda DataFactory、人工标注 — 全部不要。

---

## 5. 仓库布局

新仓库 `public/`（与 `vm-web/` 同级，文件系统在 wmbench 根下）：

```
public/
  app.py                      # 入口
  config.py                   # 只指向 SNAPSHOT_DIR，从不指向 wmbench 源
  routes/
    home.py
    leaderboard.py
    videos.py
    models.py
    media.py                  # serve snapshot/media/ 下的视频/图片
  templates/
    base.html
    home/index.html
    leaderboard/index.html
    videos/{by_law,by_model,compare}.html
    models/detail.html
    components/{navbar,footer,video_card}.html
  static/
    css/base.css              # 从 vm-web 拷贝
    js/{filters,player}.js
  scripts/
    build_snapshot.py         # 扫源 → 生成 snapshot/（含 media + index）
    verify_snapshot.py        # 校验 MANIFEST.json 的 sha256
    sync_snapshot.sh          # rsync 到部署机和备份位置
  snapshot/                   # 站点服务的唯一数据源（独立于 wmbench）
    MANIFEST.json             # build 元信息 + 每文件 sha256
    index/
      site_config.json        # 站点结构化索引
      eval_registry.frozen.json   # registry 当时快照
      paperdemo.manifest.csv      # paperdemo manifest 快照
      vis_datasets.frozen.json
      model_catalog.frozen.json   # 从 MODEL_CATALOG.py dump
    media/
      videos/<model>/<dataset>/<file>.mp4
      paperdemo/<law>/<file>.mp4
      first_frames/<dataset>/<stem>.jpg
      figs/<law>.svg            # 从 paperdemo/figs/*.pdf 转 svg
    scores/                     # 各 source_json 的拷贝（可下载）
      <evaluator>/<id>.json
  .gitignore                  # ignore snapshot/media/**, !snapshot/index/**
  README.md
```

**路径解析约定（统一过 snapshot）：**
- 视频实路径：`<SNAPSHOT_DIR>/media/videos/<model>/<dataset>/<file>`
- 站点 URL：`/video/<model>/<dataset>/<file>` → `safe_join(SNAPSHOT_DIR, "media/videos", ...)`
- paperdemo：`/paperdemo/<law>/<file>` → `<SNAPSHOT_DIR>/media/paperdemo/<law>/<file>`
- 原 source_json 下载：`/scores/<evaluator>/<id>.json`

`config.py` 只有一个 `SNAPSHOT_DIR`。**没有 `WMBENCH_ROOT`** — 站点对 wmbench 仓库零感知。

---

## 6. Snapshot builder 与 site_config.json

### Builder 流程（`scripts/build_snapshot.py`）

```
读源（read-only）                      写 snapshot/（原子替换）
─────────────────                      ───────────────────────
data/videos/<m>-<d>/*.mp4      ──cp──→ snapshot/media/videos/<m>/<d>/
data/paperdemo/<law>/*.mp4     ──cp──→ snapshot/media/paperdemo/<law>/
data/paperdemo/figs/*.pdf      ──conv→ snapshot/media/figs/*.svg (pdf2svg)
data/prompts/<d>/*.json        ──图源→ snapshot/media/first_frames/<d>/*.jpg
evals/eval_registry.json       ──cp──→ snapshot/index/eval_registry.frozen.json
data/paperdemo/manifest.csv    ──cp──→ snapshot/index/paperdemo.manifest.csv
data/vis_datasets.json         ──cp──→ snapshot/index/vis_datasets.frozen.json
videogen/runner/MODEL_CATALOG  ──dump→ snapshot/index/model_catalog.frozen.json
data/scores/**/<id>.json       ──cp──→ snapshot/scores/<evaluator>/<id>.json
                              ──算──→ snapshot/index/site_config.json
                              ──算──→ snapshot/MANIFEST.json (sha256 全表)
```

**复制策略：**
- 默认 `cp --reflink=auto`（同盘 ZFS/Btrfs/XFS 上零成本 CoW），跨盘退化到 hard copy
- 增量：`build_snapshot.py --incremental` 比对 MANIFEST 旧 sha，只拷贝改变的文件
- 原子性：先写到 `snapshot.staging/`，最后 `mv` 替换 `snapshot/`

**snapshot 视频选取范围 — 对齐 vm-web，100 量级精选集：**

vm-web 在 `static/results/` 下放 **100 task × 9 model**（来自 `scripts/download_results.sh`），不上全量。我们照搬这个量级：

| 内容 | 量级 | 说明 |
|------|------|------|
| **paperdemo** 全部 | ~130MB | `data/paperdemo/` 已按物理定律精选过，全收 |
| **humaneval 精选 100 prompt × 全部上榜模型** | ~1GB | 不存全 250，从 humaneval_set 里挑 100 条覆盖各物理定律的 prompt，每个上榜模型（含 Veo 3.1）跑过的都拷一份 |
| 真实视频 (openvid) | — | 不存（版权 + `evaluator/realvideo.md` 已说明数据质量问题） |
| 其他 dataset (wmb / physics_iq / video_phy_2) | — | 不进站点；leaderboard 引用它们的分数即可，原视频留在内部 |
| hidden / ablation / debug 输出 | — | 不存 |

**100 prompt 的挑选规则**详见 [humaneval_100.md](humaneval_100.md)：交集 gate + paperdemo 必入种子 + 按 law 配额 + 方差/覆盖/中档难度打分 + 人工 review。落地为 `snapshot/index/humaneval_100.json` 进 git。

> **判断标准**：站点上能看到 paperdemo + humaneval-100 这两个精选集对应的视频证据；leaderboard 表格里非 humaneval-100 的格点点进去显示"视频未公开，仅显示分数"。这与 vm-web 的"Hidden_40 + Open_60 = 100 task"是同一种产品形态。

**verify：** `verify_snapshot.py` 重算 sha256 vs MANIFEST，跑前每次 build 后 + 部署落地后 + 定期 cron 都跑一次。

**双副本：**
- 主：部署机本地盘 `/srv/wmbench-public/snapshot/`
- 备：另一台 VM 或 S3 桶，`sync_snapshot.sh` 用 rsync 同步
- snapshot 只增不删（每次 build 留 N 个版本：`snapshot/`, `snapshot.prev/`, `snapshot.prev2/`），出问题可秒级回滚

### site_config.json 结构

```jsonc
{
  "models": [
    { "key": "wan2.2-i2v-a14b",
      "display_name": "Wan2.2-I2V-A14B",
      "params_b": 14, "fps": 16, "frames": 81,
      "family": "Wan",
      "datasets": ["humaneval", "wmb", "video_phy_2", "physics_iq"] }
  ],
  "datasets": [
    { "key": "humaneval", "subset": "humaneval_set", "n_prompts": "...", "prompts_json": "..." }
  ],
  "leaderboard_entries": [
    /* 直接 copy from eval_registry.json，加上 model.display_name 解析 */
  ],
  "paperdemo": [
    { "law": "collision", "videos": [{ "model": "...", "dataset": "...", "src": "...", "n_ann": 8 }] }
  ],
  "videos_index": {
    "<model>::<dataset>": ["<file1>.mp4", ...]
  },
  "build_meta": { "built_at": "...", "registry_sha": "...", "manifest_sha": "..." }
}
```

builder 在拷文件之外，再做三件索引活：
1. 读 `eval_registry.json` → 过滤 `coverage > 0` 的条目，按 (model, dataset, subset, evaluator, schema) 取最新 datetime
2. 读 `data/paperdemo/manifest.csv` → 按 law 分组
3. 扫已落地的 `snapshot/media/videos/*/*/` → 建文件索引

---

## 7. 部署

复用 vm-web README §"Server Setup (Fresh Machine)" 全套：

- 选一个公网 VM（vm-web 已有一台，**复用同机**：跑两个 Flask 进程，nginx 按 `server_name` 分流，是最低成本路径）
- 域名：另起一个，比如 `wmbench.<...>.com`，CNAME 指 vm-web 的 IP
- 同机部署的 nginx 多 server block 例子：

```nginx
server {
  listen 443 ssl;
  server_name wmbench.example.com;
  ssl_certificate /etc/letsencrypt/live/wmbench.example.com/fullchain.pem;
  client_max_body_size 100M;
  location / { proxy_pass http://127.0.0.1:5001; }   # public 用 5001，vm-web 用 5000
  location /video/ { proxy_pass http://127.0.0.1:5001; proxy_buffering off; }
}
```

- gunicorn：`gunicorn --workers 4 --bind 127.0.0.1:5001 app:app`
- **不挂 NFS、不依赖 wmbench 仓库**：部署机本地盘上有完整 `snapshot/`，断网/源库被删都能继续 serve
- 部署 = 三步：(1) 在 builder 机跑 `build_snapshot.py`；(2) `rsync -a snapshot/` 到部署机 `/srv/wmbench-public/snapshot/`；(3) 部署机 `verify_snapshot.py` 通过 → systemd reload gunicorn
- 部署机磁盘：估算见 `docs/exp-results/videogen/` 的体量统计；按 paperdemo + 在册 leaderboard 涉及的子集计，远小于全量 `data/videos/`

> 也可考虑**纯静态导出**（Flask-Frozen 或自写 builder 把所有 HTML 渲染成文件 + 视频做软链）→ 直接 S3/CloudFront 托管。如果后期不需要交互筛选（leaderboard 切片）可切静态。v1 先动态。

---

## 8. 分阶段任务

### Phase 0 — 骨架（半天）
- [ ] 拷贝 vm-web 骨架到 `public/`（routes/templates/static/css 同名结构，去掉 generator/datafactory 路由）
- [ ] 改 `config.py`，加 `WMBENCH_ROOT`、新域名
- [ ] 跑通 `home` + 一个 stub `/leaderboard/`，`./start.sh` 本地能起

### Phase 1 — Snapshot builder + Leaderboard
- [ ] `scripts/build_snapshot.py`：拷视频/JSON + 生成 `site_config.json` + 写 `MANIFEST.json`
- [ ] `scripts/verify_snapshot.py`：sha256 全表校验
- [ ] `scripts/sync_snapshot.sh`：rsync 主备 + 保留 N 个历史版本
- [ ] `routes/leaderboard.py`：从 `snapshot/index/site_config.json` 读，query params 筛选 + 表格 + raw JSON 下载
- [ ] 引文 caveat：domain-pooled vs macro-averaged 注脚

### Phase 2 — Video Gallery
- [ ] `routes/videos.py` + `routes/media.py`（serve `/video/...` 和 `/paperdemo/...`，path traversal 防护）
- [ ] `by_law` 页：从 `paperdemo/manifest.csv` 渲染
- [ ] `by_model` 页：从 `videos_index` 渲染
- [ ] `/videos/compare?prompt_id=` 跨模型对比

### Phase 3 — Models 详情 + Home featured
- [ ] `routes/models.py` 解析 `MODEL_CATALOG`
- [ ] Home featured comparison（一个写死的 law）
- [ ] About 页 + bib + license

### Phase 4 — 部署 + 双副本
- [ ] 注册域名 + DNS A/CNAME
- [ ] vm-web 部署机加 nginx server block + certbot 拿证
- [ ] gunicorn systemd unit（指向 `/srv/wmbench-public/snapshot/`）
- [ ] 备份 VM / S3 桶接好 `sync_snapshot.sh` 的目标，跑通一次完整同步
- [ ] cron：每周 `verify_snapshot.py` 跑一次 + 失败告警
- [ ] 烟测：所有视频能播、所有 leaderboard 切片有数、断 NFS 后站点仍工作（确认无回查）

### Phase 5（可选） — 静态化
- [ ] Flask-Frozen 把动态页渲染成静态文件 → 视频走 NFS 或 S3，进一步降运维成本

---

## 9. 风险 & 决策点

| 风险 | 处理 |
|------|------|
| 视频体量大 | snapshot 按 §6 限定 paperdemo 全集 + humaneval-100 × 上榜模型，约 ~1GB 量级；ffmpeg 转 720p 缓存进 `snapshot/media/cache/` |
| 评测数据不断更新 | builder cron 每日增量；旧版 snapshot 保留 N 份可回滚 |
| **wmbench 主仓被删 / NFS 失联** | snapshot 完全独立，部署机本地盘 + 异地副本各一份；verify_snapshot 定期校验完整性 |
| **builder 机和部署机不在一起** | builder 输出可单独 tar.zst 后传输；MANIFEST 校验保证传输完整 |
| Reviewer 双盲期 | 先用占位作者名 + 不放 GitHub org 链接；论文录用后再切实名 |
| 真实视频 (openvid) 版权 | snapshot **不拷** openvid 视频，只存元数据 + YouTube 链接。参考 `evaluator/realvideo.md` |
| 模型名 vs key 不一致 | 全站以 `MODEL_CATALOG` key 为主键，`display_name` 仅展示用 |

---

## 10. 与现有系统的边界

| 系统 | 用途 | 对外？ | 与 public 关系 |
|------|------|--------|----------------|
| `evals/vis_eval_server.py` | 内网 viewer，含人工辅助审查 | 否 | 思路相同，public 是其只读公开版 |
| `evals/human_eval/` | 标注 app | 否 | public 显示 n_ann 列即可，不暴露原始标注 |
| `vm-web/` | VBVR Suite 的产品站 | 是 | 复用 nginx/视觉模板，**不混库** |
| `evals/eval_registry.json` | 评测唯一索引 | — | public 唯一权威数据源 |

---

## 11. 后续记录入口

- 实施进度 / commit 摘要 → `docs/exp-results/public/` （新建）
- 部署变更（域名、cert 续期）→ 本目录加 `deploy.md`
- 视觉/页面 mock → 本目录加 `mock/` 子目录
- snapshot 体量 / 副本拓扑 / 备份位置清单 → 本目录加 `snapshot.md`

---

## 12. 与 wmbench `.gitignore` 的关系

wmbench 主仓 `.gitignore` 已忽略 `*.mp4 / *.json / *.jpg / data/videos/`，意思是**主仓不背视频/分数 JSON 的版本**。snapshot 走"文件系统多副本"而非 git。

- `public/snapshot/media/**`、`public/snapshot/scores/**` → 新写 `public/.gitignore` 显式忽略
- `public/snapshot/index/*.json`、`public/snapshot/MANIFEST.json` → 用 `!` 白名单进 git，作为**轻量 metadata 历史**（小文件，几 MB 级，可承受）
- 这样：源数据真被删时，至少 git 里还有"曾经存在哪些 (model, dataset, video_id, score)"的元数据线索，配合任一副本的 media 即可恢复
