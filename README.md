# OurTaiko Constants

读取本地 TJA 谱面，通过 TJA Analysis API 分析，计算原始指标并进行全局校准，自动导出歌曲定数。一次运行即可完成 batch 和 extract。

## Quick start

准备 Python 3.10+、uv，以及本地完整的 Songs 谱面目录。首次安装依赖和请求 API 需要网络；谱面文本会发送至配置的 TJA Analysis API（默认 `https://tja-analysis.ourtaiko.org`）。

以下命令均在仓库根目录执行。

1. 安装依赖：

   ```sh
   uv sync --locked
   ```

2. 复制配置文件（PowerShell）：

   ```powershell
   Copy-Item .env.example .env
   ```

   macOS / Linux 使用 `cp .env.example .env`。已有 `.env` 时跳过复制，避免覆盖配置。

3. 编辑根目录 `.env`，填写本机谱面目录：

   ```dotenv
   SONGS_BASE_DIR='C:/path/to/Songs'
   ```

   Songs 内的相对路径需与 ese_mapping 匹配。配置优先级：`--base-dir` > 环境变量 > `.env`。

4. 运行全量计算：

   ```sh
   uv run src/batch_workflow.py
   ```

   自动生成根目录的 `constants.json`（edit / oni / hard 精简结果）和 `raw_constants.json`（完整分支结果、校准值及错误信息），不需要再手动运行 extract。

## 常用命令

```sh
# 小批量检查：生成 *.sample.json，不覆盖默认正式输出
uv run src/batch_workflow.py --limit 5 --workers 4

# 指定 Songs 目录
uv run src/batch_workflow.py --base-dir "D:/Songs"

# 忽略并更新本次歌曲的缓存
uv run src/batch_workflow.py --refresh

# 完全不读取或写入缓存
uv run src/batch_workflow.py --no-cache

# 自定义输出目录（精简结果默认写到 out/constants.json）
uv run src/batch_workflow.py --output out/raw.json

# 只重新提取已有结果，不调用 API
uv run src/extract_song_constants.py raw_constants.json -o constants.json

# 查看全部选项
uv run src/batch_workflow.py --help
```

缓存位于 `.cache/`：第一层保存 API 分析，第二层保存原始指标。原始算法代码变化后自动重算指标；仅修改最终换算公式时复用指标，但始终重新全局校准。即使命中缓存，batch 仍需联网获取映射，并读取本地谱面检查内容变化。

小批量使用子集校准，定数不能当作正式结果。部分歌曲失败时仍会输出成功部分，请检查终端失败计数及 raw 文件的 `errors`。`--refresh` 和 `--no-cache` 不能同时使用。

## 项目结构

```text
src/
  batch_workflow.py          # 批量计算及自动导出入口
  extract_song_constants.py  # 独立提取入口
  tja_analysis.py            # API 分析及原始指标
  rating.py                  # 校准及最终定数换算
  algorithms/                # 各维度原始算法
docs/
  workflow.md                # 公式和详细流程
Tests/                       # 测试和 TJA 样本
constants.json               # 保持原有发布路径的精简定数
.env.example                 # 本地配置示例
pyproject.toml / uv.lock      # 依赖定义及锁文件
```

`.env`、`.cache/`、`raw_constants.json` 和 `*.sample.json` 不纳入 Git。输出和缓存的相对路径基于执行命令时的目录；`.env` 始终从仓库根目录读取。

## 开发与测试

```sh
uv run python -m unittest discover -s Tests -p "test_*.py"
```

测试离线运行，不请求 API、不覆盖正式定数。算法说明见 [docs/workflow.md](docs/workflow.md)。本次目录整理不改变公式或全局校准范围。
