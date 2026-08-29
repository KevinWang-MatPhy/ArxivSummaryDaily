# ArXiv Summary Daily

一个自动获取和总结 arXiv 论文的工具。每天自动检索最新的论文，并使用 AI 生成摘要。本项目旨在帮助研究人员快速了解最新的研究动态，节省阅读论文的时间。
## 介绍
本项目是一个自动化的 arXiv 论文摘要生成器，使用 Python 和 GitHub Actions 实现。它可以定期从 arXiv 上获取最新的论文，并使用 AI 模型生成摘要。生成的摘要将被自动部署到 GitHub Pages 上，方便用户查看。
这个repo会将每天更新的超导-强关联-材料-纳米四个领域论文摘要，在每天北京时间12:00前后自动部署在[这个链接](https://dong-zehao.github.io/ArxivSummaryDaily/)中。若有其他领域或者关键词搜索的需求，请fork本仓库并按照下面的指南配置。

## 功能特点

- 支持利用Github Actions在服务器端定时更新并部署到GitHub Pages
- 支持多个 arXiv 分类的论文检索
- 自动过滤重复论文
- 使用 AI 生成论文摘要
- 支持增量更新，避免重复处理
- 可配置的搜索参数和关键词
- 输出示例

<img src="img/overview.png" alt="overview" style="zoom: 33%;" />

## 配置

在 `config/settings.example.py` 中配置：

- 搜索分类 (CATEGORIES)
- 搜索关键词 (QUERY)
- AI 模型的 API 密钥（环境变量 `LLM_API_KEY`，对应 `LLM_CONFIG` 中的 `api_key`）- **注意：** 如果使用 GitHub Actions，建议通过 Secrets 配置 API 密钥。
- OpenAI 兼容接口配置：默认接入 Agnes 2.5 Flash，Base URL 为 `https://api.agnes-ai.cn/v1`，模型名为 `agnes-2.5-flash`。
- 其他搜索参数 (SEARCH_CONFIG)
- 输出配置 (OUTPUT_DIR): 设置存放AI summary的文件路径 (本地运行时使用)

默认分类已针对电子显微学、材料科学与凝聚态物理交叉研究扩充为
4 个主题、15 个 arXiv 分类：

- 材料与凝聚态：材料科学、介观与纳米物理、强关联、超导、无序体系、软物质等。
- 显微仪器与成像：仪器和探测器、应用物理、电子光学与相位恢复。
- 原子尺度材料与模拟：化学物理、原子/分子团簇、计算物理。
- 显微数据分析：4D-STEM、电子断层成像、图像重建、去噪和逆问题相关分类。

`RESEARCH_TOPICS` 是检索和网页共同使用的唯一分类定义，并自动生成
`CATEGORY_GROUPS`、`CATEGORY_LABELS` 和 `CATEGORIES`。若需要进一步覆盖通用
计算机视觉论文，可在“显微数据分析”主题的 `categories` 中加入 `cs.CV`；默认不
启用，以避免无关论文数量过多。

检索会依次查询全部 15 个分类，而不是把分类合并成一个容易被高投稿量领域占满的
OR 查询。默认每类最多选择 8 篇、去重后每次最多总结 120 篇。每个分类拥有独立的
增量游标；跨分类论文按 arXiv ID 去重，单个分类临时失败不会丢失其他分类的结果。
旧版 `last_run.json` 会自动迁移，历史摘要中的论文 ID 也会用于防止重复总结。

生成的 GitHub Pages 首页固定展示上述 4 个主题和 15 个 arXiv 分类，并支持标题与
摘要搜索、时间范围、日期排序和结果计数。主题与分类按钮会显示论文数量；本期为
零的分类仍会显示为禁用状态，便于确认完整覆盖范围。论文以卡片形式展示，在手机
和桌面设备上均可直接浏览。

### 理论、计算与实验方法分类

摘要生成后，`arxivclassify` 会调用同一套 OpenAI 兼容 LLM 接口，对整理好的中文摘要
进行研究技术方法分类。分类支持 `理论`、`计算`、`实验` 多标签，并记录主方法、
置信度和简短判定依据。低置信度或失败结果显示为“未判定”，不会阻止网页发布。

```bash
arxivclassify --data-dir ./data
```

分类结果写入论文区块的 `data-methods` 属性，并缓存在
`data/method_classifications.json`。摘要内容没有变化时会复用缓存；历史摘要中缺失的
分类也会自动补齐。网页可组合使用“交叉研究主题 × 研究技术方法 × arXiv 分类”筛选。

相关参数位于 `METHOD_CLASSIFICATION_CONFIG`。如需使用独立模型，可设置环境变量
`METHOD_CLASSIFICATION_MODEL`；默认沿用 `LLM_MODEL`。

## 使用方法 1: GitHub Actions 自动部署 (推荐)

本项目配置了 GitHub Actions 工作流 (`.github/workflows/pages.yml`)，可以实现全自动的论文检索、总结和网站部署。

**工作流程:**

1.  **触发:**
    *   每周一至周五北京时间中午 12:00 自动运行。
    *   或者也可以在Github Actions界面中手动触发。
2.  **执行:**
    *   自动拉取最新代码。
    *   根据 `config/settings.example.py` 中的 `CATEGORIES` 和 `QUERY` 设置（或 Actions 文件中硬编码的参数，如果存在）搜索 arXiv 论文。
    *   使用配置的 AI 模型生成论文摘要。**需要配置 GitHub Secrets `LLM_API_KEY` (见设置步骤)**。
    *   将最新的摘要 Markdown 文件生成为 `index.md`。
    *   使用 Jekyll 将结果部署到 GitHub Pages。

**设置步骤:**

1.  **Fork 本仓库**: 如果你想在自己的 GitHub 账号下运行，请先 Fork 这个仓库。
2.  **配置 Secrets**:
    *   在你的 GitHub 仓库页面，进入 `Settings` -> `Secrets and variables` -> `Actions`。
    *   点击 `New repository secret`。
    *   创建一个名为 `LLM_API_KEY` 的 Secret，其值为你的 AI 模型 API 密钥。工作流将自动读取这个 Secret。
    *   如需覆盖默认接口或模型，可在 Actions 的 Variables 中设置 `LLM_BASE_URL` 和 `LLM_MODEL`。
3.  **(可选) 修改配置**:
    *   如果你想更改搜索的分类或关键词，请直接修改仓库中的 `config/settings.example.py` 文件并提交。工作流将使用最新提交的配置。
    *   *注意：* 不要在 `settings.example.py` 中直接写入 API 密钥，应始终使用 GitHub Secrets。
4.  **运行Github Actions**:
    *   首次运行时，请在你的 GitHub 仓库页面，进入 `Actions` -> `pages`。
    *   点击`Run workflow`，将在Actions中开启工作流，并自动创建仓库的`gh-pages`分支，自动将输出的markdown文件加入其中
4.  **配置 Github Pages**:
    *   在你的 GitHub 仓库页面，进入 `Settings` -> `Pages`。
    *   部署源 (`Source`) 设置为 `Deploy from a branch`
    *   选择分支 (`Branch`) 为 `gh-pages`，
    *   文件夹 (`folder`) 为 `/(root)`。
5.  **查看结果**: 工作流运行成功后，生成的论文摘要网站将发布在你的 GitHub Pages 地址 (通常是 `https://<your-username>.github.io/ArxivSummaryDaily/`)。

## 使用方法 2: 本地运行

### 安装

1. 克隆仓库并进入项目目录：
```bash
git clone https://github.com/dong-zehao/ArxivSummaryDaily.git
cd ArxivSummaryDaily
```

2. 安装依赖：
```bash
pip install -r requirements.txt
```

3. 安装命令行工具：
```bash
pip install -e .
```

安装完成后，`arxivsummary` 命令将被添加到系统路径中，可以在任何目录下直接使用。

### 配置

参考前面的配置方法，将 `config/settings.example.py` 复制为 `config/settings.py`。推荐通过环境变量提供密钥，不要把真实密钥写入仓库：

```bash
export LLM_API_KEY="你的 Agnes API Key"
# 以下两项已有默认值，通常无需设置
export LLM_BASE_URL="https://api.agnes-ai.cn/v1"
export LLM_MODEL="agnes-2.5-flash"
```

程序使用 OpenAI 兼容的 Chat Completions 协议，请求地址为
`$LLM_BASE_URL/chat/completions`，并通过 `Authorization: Bearer $LLM_API_KEY`
认证。接口参数可参考 [Agnes 2.5 Flash 官方文档](https://www.agnes-ai.cn/zh-Hans/docs/agnes-25-flash)。

### 使用
运行主程序：
```bash
arxivsummary
```

可选参数：
- `--query`: 设置搜索关键词，默认使用配置文件中的 QUERY
- `--categories`: 设置要搜索的 arXiv 分类，可指定多个，默认使用配置文件中的 CATEGORIES
- `--max-results`: 设置要获取的最大论文数量，默认使用配置文件中的设置
- `--output-dir`: 设置输出目录，默认使用配置文件中的 OUTPUT_DIR

使用示例：
- 搜索关键词`nickelate`，只搜索最新的10篇文献
    ```bash
    arxivsummary --query "nickelate" --max-results 10
    ```
- 搜索关键词`cuprate`，在凝聚态-超导或者凝聚态-强关联领域搜索，输出在指定的文件夹中
    ```bash
    arxivsummary --query "cuprate" --categories cond-mat.supr-con cond-mat.str-el --output-dir "./cuprate_summaries"
    ```

## 输出 (本地运行)

- 论文总结保存在 `OUTPUT_DIR/summary_yyyyMMdd_hhmmss.md`
