# Chinese Elite (中国精英)

一个实验性项目，基于LLMs (大语言模型) 解析公共数据、并与官方来源交叉引用，自动绘制中国精英的关系网络。

点击 [这里](https://anonym-g.github.io/Chinese-Elite) 开始体验。

Telegram 群组链接: https://t.me/ChineseEliteTeleGroup

An experimental project, that automatically maps the relationship networks of Chinese Elites by parsing public data using LLMs and cross-referencing with official sources.

Click [Here](https://anonym-g.github.io/Chinese-Elite) to begin.

Telegram Group Link: https://t.me/ChineseEliteTeleGroup

目录：

- [Chinese Elite (中国精英)](#chinese-elite-中国精英)
  - [中文版](#中文版)
    - [项目简介](#项目简介)
    - [项目结构](#项目结构)
    - [数据结构](#数据结构)
    - [可视化](#可视化)
    - [如何部署](#如何部署)
    - [项目状态](#项目状态)
    - [免责声明](#免责声明)
    - [贡献](#贡献)
    - [数据修正详解](#数据修正详解)
    - [关于 Pull Request](#关于-pull-request)
  - [English Version](#english-version)
    - [Project Introduction](#project-introduction)
    - [Project Structure](#project-structure)
    - [Data Structure](#data-structure)
    - [Visualization](#visualization)
    - [How to Deploy](#how-to-deploy)
    - [Project Status](#project-status)
    - [Disclaimer](#disclaimer)
    - [Contributing](#contributing)
    - [Detail Explanation of Data Revisions](#detail-explanation-of-data-revisions)
    - [About Pull Requests](#about-pull-requests)

-----

## 中文版

### 项目简介

本项目利用大语言模型的力量，从维基百科等公开数据源中提取信息，创建一个可自我更新、公开可访问的中国政商精英关系图谱数据库，并提供可视化前端。核心目标，是为研究人员、记者、以及任何对理解中国权力结构感兴趣的人士，提供一个透明、可验证且持续改进的分析工具。

### 项目结构

```
.
├── .cache/                     # 存放缓存文件 (Q-Code、链接状态、页面热度)
├── .github/
│   └── workflows/
│       ├── daily_post.yml      # GitHub Action: 每日定时在Telegram频道发布“历史上的今天”
│       ├── pr_auto_merger.yml  # GitHub Action: 自动审查、合并单文件数据类 PR
│       └── update_data.yml     # GitHub Action: 自动化数据更新流水线
├── bot_app/                    # Telegram 问答机器人 (基于 Flask + Webhook)
│   ├── __init__.py
│   ├── app.py                  # Flask 应用，提供 Webhook 入口
│   ├── bot.py                  # 机器人核心逻辑，处理消息并与LLM交互
│   └── set_webhook.py          # 用于设置 Webhook 的脚本
├── data/                       # 存放LLM原始提取的JSON数据的根目录
│   ├── person/
│   │   └── ...                 # 按类别存放LLM原始提取的JSON数据
│   ├── ...
│   ├── processed_files.log     # 记录已合并处理的文件名
│   └── LIST.md                 # 待处理的实体种子列表 (按热度排序)
├── data_to_be_cleaned/         # 存放由脚本分离出的待手动清理的数据
├── docs/                       # 前端可视化页面 (通过 GitHub Pages 部署)
│   ├── assets/                 # 存放前端静态资源 (如Logo)
│   ├── data/
│   │   ├── nodes/              # 按需加载的“简单数据库”，每个节点一个文件夹
│   │   │   ├── Q12345/
│   │   │   │   └── node.json
│   │   │   └── ...
│   │   ├── initial.json        # 前端首次加载的核心图谱数据
│   │   └── name_to_id.json     # 全局搜索用的名称-ID映射索引
│   ├── locales/                # 国际化 (i18n) 语言包
│   │   ├── en.json             # 英文
│   │   └── zh-cn.json          # 简体中文
│   ├── modules/                # 前端 JavaScript 模块
│   │   ├── config.js           # 前端-全局配置
│   │   ├── dataProcessor.js    # 前端-数据加载、处理与过滤
│   │   ├── graphView.js        # 前端-基于 PixiJS (WebGL) 的视图渲染与交互
│   │   ├── i18n.js             # 前端-国际化处理模块
│   │   ├── state.js            # 前端-全局状态管理
│   │   ├── uiController.js     # 前端-UI控件与事件处理
│   │   └── utils.js            # 前端-辅助工具函数
│   ├── main.js                 # 前端-主入口文件
│   ├── master_graph_qcode.json # 合并后的完整主图谱文件
│   ├── index.html              # 可视化页面 HTML
│   └── style.css               # 页面样式
├── logs/                       # 存放后端运行日志
├── scripts/                    # 后端 Python 业务逻辑脚本
│   ├── clients/                # 封装与外部API交互的客户端
│   │   └── wikipedia_client.py # 维基百科API客户端 (获取Wikitext, Q-Code等)
│   ├── prompts/                # 存放所有LLM的提示词 (Prompt) 文本
│   │   └── ...
│   ├── services/               # 封装核心业务逻辑的服务
│   │   ├── graph_io.py         # 封装图谱文件读写操作
│   │   └── llm_service.py      # 封装与Google GenAI模型的所有交互
│   ├── api_rate_limiter.py     # API速率限制器
│   ├── check_pageviews.py      # (异步) 检查实体热度并重排序列表
│   ├── clean_data.py           # 深度数据清洗与维护脚本
│   ├── config.py               # 后端-所有路径和模型配置
│   ├── generate_frontend_data.py # 生成所有前端数据文件
│   ├── merge_graphs.py         # 增量数据智能合并脚本
│   ├── process_list.py         # 根据列表处理实体的核心脚本
│   ├── scheduled_tasks.py      # 定时任务脚本 (如“历史上的今天”)
│   ├── utils.py                # 后端-通用辅助工具函数
│   └── validate_pr.py          # PR 验证脚本
├── .env                        # (需自行创建) 存放 API 密钥和 Telegram Token
├── .gitignore
├── README.md                   # 本文件
├── requirements.txt            # Python 依赖列表
├── run_pipeline.py             # 完整后端流水线一键执行脚本
└── run_set_webhook.py          # 设置 Telegram Webhook 的入口脚本
```

### 数据结构

系统输出的核心是JSON对象，包含 `nodes` (节点) 和 `relationships` (关系) 两个关键部分。

**节点** (`nodes`) 是图中的实体，其属性包括唯一的 `id` (优先使用 Wikidata Q-Code)、`type` (实体类型)、`name` (名称列表) 以及 `properties` (属性，包含活跃时期、生卒年月、描述等详细信息)。

预设的节点类型包括：`Person`, `Organization`, `Movement`, `Event`, `Location`, `Document`。

**关系** (`relationships`) 定义了节点之间的连接，其属性包括 `source` (源节点id)、`target` (目标节点id)、`type` (关系类型) 以及 `properties` (包含关系起止时间、职位、补充描述等)。目前，关系类型包括以下几种：

| 类型 | 方向 | 源节点类型 | 目标节点类型 |
| :--- | :--- | :--- | :--- |
| `BORN_IN` (出生于) | 有向 | 人物 | 地点 |
| `ALUMNUS_OF` (就读于) | 有向 | 人物 | 组织 |
| `MEMBER_OF` (成员) | 有向 | 人物, 组织 | 组织 |
| `CHILD_OF` (子嗣) | 有向 | 人物 | 人物 |
| `SPOUSE_OF` (配偶) | **无向** | 人物 | 人物 |
| `SIBLING_OF` (兄弟姐妹) | **无向** | 人物 | 人物 |
| `LOVER_OF` (情人) | **无向** | 人物 | 人物 |
| `RELATIVE_OF` (亲属) | **无向** | 人物 | 人物 |
| `FRIEND_OF` (交好) | **无向** | 人物, 组织 | 人物, 组织 |
| `ENEMY_OF` (交恶) | **无向** | 人物, 组织 | 人物, 组织 |
| `SUBORDINATE_OF` (从属于) | 有向 | 人物, 组织 | 人物, 组织 |
| `MET_WITH` (会面) | **无向** | 人物 | 人物 |
| `PUSHED` (推动) | 有向 | 人物, 组织, 事件, 运动, 文件 | 人物, 组织, 事件, 运动, 文件 |
| `BLOCKED` (阻碍) | 有向 | 人物, 组织, 事件, 运动, 文件 | 人物, 组织, 事件, 运动, 文件 |
| `INFLUENCED` (影响) | 有向 | 人物, 组织, 事件, 运动, 文件 | 人物, 组织, 事件, 运动, 文件, 地点 |
| `FOUNDED` (创立) | 有向 | 人物, 组织 | 组织 |

### 可视化

项目在 `docs/` 目录下提供了一个交互式前端可视化界面。它使用 PixiJS (WebGL) 进行高性能渲染，并结合 D3.js 进行物理布局模拟，能够流畅地展示数千个节点。

该界面支持按时间范围筛选节点和关系，通过图例动态显示或隐藏不同类型的节点。

该页面已通过 GitHub Pages 部署，您可以通过[此链接](https://anonym-g.github.io/Chinese-Elite/)进行访问。

### 如何部署

1.  克隆本仓库至本地。
2.  安装所有必要的Python依赖包：`pip install -r requirements.txt`。
3.  在项目根目录创建 `.env` 文件，并设置你的 `GOOGLE_API_KEY`。
4.  在 `data/LIST.md` 中按分类填入你希望抓取的实体名称。
5.  执行 `python run_pipeline.py` 脚本，它将自动完成完整的后端数据处理流程。
6.  为避免浏览器本地文件访问限制，建议通过一个简单的本地HTTP服务器（如 `python -m http.server`）启动服务，然后在浏览器中访问。

### 项目状态

本项目目前处于**开发**阶段。

### 免责声明

本工具呈现的数据仅供参考与研究之用。所有信息均由程序自动从公开来源生成，并可能受到源数据固有的延迟和潜在不准确性影响。在使用时，应通过数据中提供的官方链接或其他一手资料进行独立核实。

### 贡献

这是一个开源项目，非常欢迎任何形式的贡献。无论你是开发者、数据科学家还是领域专家，都可以通过改进提取脚本、优化LLM提示、扩展种子列表、增强前端可视化或报告数据不准确之处来提供帮助。请自由 Fork 本仓库、开启 Issue 或提交 Pull Request。

为保证社区用户可以更轻松地参与维护，本项目集成了一个由 LLM 驱动的自动化 Pull Request (PR) 处理系统。

该系统仅审查数据类 PR，即仅处理对两份项目文件的修改：一是修正 `docs/master_graph_qcode.json` 文件中的数据，二是向 `data/LIST.md` 文件中添加新的实体条目。

进行这类贡献时，您必须遵循一个核心规则：每一次 Pull Request，只能包含对单个文件的修改。包含多个文件改动的 PR 将被该自动化系统忽略。

UTC 时间 1 ~ 16 点，大约每半个小时，系统会自动进行一次 PR 审查。

只要 PR 仅对上述两个文件之一进行了修改，AI 审查员会评估更改是否合理。

如果更改被判定为有意义，PR 将被自动合并；反之，如果被判定为无意义或存在问题，PR 将被自动关闭并附上说明。

另外，由于本项目配置了专门的自动化数据更新流水线（UTC 时间 18 点开始运行，至多持续 6 小时），若您当日的数据类 PR 没有被及时合并，次日可能会落后于主仓库版本。

因此，请每日更新您用于提交 PR 的 Fork 仓库分支。

### 数据修正详解

1. 修改 `docs/master_graph_qcode.json` 文件 (主数据文件) 。您可以克隆仓库，手动编辑此文件以修正错误的节点属性或关系，然后提交一个 Pull Request。

请注意，LLM在提取数据时存在一些普遍问题，例如：它可能会生成一些较弱的冗余关系（如同时生成 `BLOCKED`、`INFLUENCED`，后者通常可直接删去）；对于部分有向关系（如 `INFLUENCED`），它有时会弄反源节点和目标节点。我们尤其欢迎针对这类数据错误的修正。

2. 修改种子列表。在 `data/LIST.md` 的前6个栏目中添加实体名称，可以让脚本在后续运行时检索对应的维基百科页面。

注意，考虑到脚本的工作方式，您必须添加完整的维基百科页面名。以下举两例说明：

49-54年的中央人民政府，维基百科的页面链接是 [https://zh.wikipedia.org/wiki/中华人民共和国中央人民政府_(1949年—1954年)](https://zh.wikipedia.org/wiki/中华人民共和国中央人民政府_(1949年—1954年))

那么，您需要复制粘贴完整的页面名，"中华人民共和国中央人民政府 (1949年—1954年)" (下划线可以正常换成空格，其他部分必须与页面名一致，包括破折线、两个"年"字)，并将其添加到 `data/LIST.md` 文件的 Organization (组织) 条目下。

蒋介石，维基百科的页面链接是 [https://zh.wikipedia.org/wiki/蔣中正](https://zh.wikipedia.org/wiki/蔣中正)

那么，您需要复制粘贴 "蔣中正" (繁体"蔣"，以便查重)，并将其添加到 `data/LIST.md` 文件的 Person (人物) 条目下。

```data/LIST.md
Person
...
蔣中正

Organization
...
中华人民共和国中央人民政府 (1949年—1954年)

...

```

这样，GitHub Action Bot在下次运行 `.github/workflows/update_data.yml` 时，将自动处理这两个页面，利用LLM提取其中的有关信息。——如果LLM API用量没有超限的话。

另外，在添加这些词项时，请用 `Shift + F` 随手查一下重。若词项已经在前六条，则不必添加。若条目在 new 条目下，请随手删除 (new 下面的词条不会被处理，相当于一个缓冲池) 。

```data/LIST.md
Person
...
蔣中正

...

new
...
~~蔣中正~~
...

```

### 关于 Pull Request

在提交贡献时，理解 GitHub Pull Request 的工作机制至关重要。

一个 PR，是一个指向您 Fork 仓库中特定分支的动态引用。这意味着，在您的 PR 被合并或关闭之前，任何推送到该源分支的新提交都会自动同步到这个 PR 中。

这个特性可能会导致意外的错误。

例如，如果您在提交一个 PR 后，没有等待它被合并，就继续在同一个分支上进行其他修改，那么这些可能尚未完成或不相关的改动也会被附加到原有的 PR 里，很可能导致 AI 审查失败。

因此，我们强烈建议您遵循以下稳妥的贡献流程：

1. 在您的 Fork 仓库中完成对单个文件的修改。

2. 提交并推送至您的 Fork 仓库，然后向主仓库发起 Pull Request。

3. 耐心等待。在您的 PR 被自动合并或关闭之前，请不要向创建该 PR 的分支推送任何新的提交。

4. 当您的 PR 被成功合并后，请先将您的本地仓库与主仓库（origin）的最新状态同步（例如，通过 git pull origin main）。

5. 完成同步后，再开始您的下一次贡献。

遵循此流程，可以确保您的每一次贡献都是一个独立、干净的单元，从而保证自动化系统能够顺利地进行处理。

您可以进一步参考本项目 Pull Request 页面的历史 PR：[https://github.com/anonym-g/Chinese-Elite/pulls?q=is%3Apr+is%3Aclosed](https://github.com/anonym-g/Chinese-Elite/pulls?q=is%3Apr+is%3Aclosed)

-----

## English Version

### Project Introduction

This project leverages the power of Large Language Models to extract information from public data sources like Wikipedia, creating a self-updating, publicly accessible graph database of relationship networks among China's political and business elites, complete with a visualization front-end. The core objective is to provide a transparent, verifiable, and continuously improving analytical tool for researchers, journalists, and anyone interested in understanding China's power structures.

### Project Structure

```
.
├── .cache/                     # Stores cache files (Q-Codes, link status, page views)
├── .github/
│   └── workflows/
│       ├── daily_post.yml      # GitHub Action: Daily "On This Day" Telegram Channel post
│       ├── pr_auto_merger.yml  # GitHub Action: Auto-review and merge single-file data PRs
│       └── update_data.yml     # GitHub Action: Workflow for automated data updates
├── bot_app/                    # Telegram Q&A Bot (based on Flask + Webhook)
│   ├── __init__.py
│   ├── app.py                  # Flask application, provides Webhook entry point
│   ├── bot.py                  # Core bot logic, handles messages and interacts with LLM
│   └── set_webhook.py          # Script for setting the webhook
├── data/                       # Root directory for raw JSON data extracted by LLM
│   ├── person/
│   │   └── ...                 # Raw JSON data extracted by LLM, categorized
│   ├── ...
│   ├── processed_files.log     # Logs filenames that have been merged and processed
│   └── LIST.md                 # Seed list of entities to be processed (sorted by popularity)
├── data_to_be_cleaned/         # Stores data separated by scripts for manual cleaning
├── docs/                       # Frontend visualization page (deployed via GitHub Pages)
│   ├── assets/                 # Stores static assets for frontend (e.g., logos)
│   ├── data/
│   │   ├── nodes/              # "Simple database" for on-demand loading, one folder per node
│   │   │   ├── Q12345/
│   │   │   │   └── node.json
│   │   │   └── ...
│   │   ├── initial.json        # Core graph data for initial frontend load
│   │   └── name_to_id.json     # Name-to-ID mapping index for global search
│   ├── locales/                # Internationalization (i18n) language packs
│   │   ├── en.json             # English
│   │   └── zh-cn.json          # Simplified Chinese
│   ├── modules/                # Frontend JavaScript modules
│   │   ├── config.js           # Frontend - Global configuration
│   │   ├── dataProcessor.js    # Frontend - Data loading, processing, and filtering
│   │   ├── graphView.js        # Frontend - PixiJS (WebGL) based view rendering and interaction
│   │   ├── i18n.js             # Frontend - Internationalization handling module
│   │   ├── state.js            # Frontend - Global state management
│   │   ├── uiController.js     # Frontend - UI controls and event handling
│   │   └── utils.js            # Frontend - Utility functions
│   ├── main.js                 # Frontend - Main entry point
│   ├── master_graph_qcode.json # The final, complete master graph file
│   ├── index.html              # Visualization page HTML
│   └── style.css               # Page stylesheet
├── logs/                       # Stores backend runtime logs
├── scripts/                    # Backend Python business logic scripts
│   ├── clients/                # Clients for interacting with external APIs
│   │   └── wikipedia_client.py # Wikipedia API client (fetches Wikitext, Q-Codes, etc.)
│   ├── prompts/                # Stores all prompt text files for the LLM
│   │   └── ...
│   ├── services/               # Services encapsulating core business logic
│   │   ├── graph_io.py         # Encapsulates graph file I/O operations
│   │   └── llm_service.py      # Encapsulates all interactions with Google GenAI models
│   ├── api_rate_limiter.py     # API rate limiter
│   ├── check_pageviews.py      # (Async) Checks entity popularity and reorders the list
│   ├── clean_data.py           # Deep data cleaning and maintenance script
│   ├── config.py               # Backend - Configuration for all paths and models
│   ├── generate_frontend_data.py # Generates all frontend data files
│   ├── merge_graphs.py         # Script for intelligent incremental data merging
│   ├── process_list.py         # Core script for processing entities from the list
│   ├── scheduled_tasks.py      # Scheduled tasks script (e.g., "On This Day")
│   ├── utils.py                # Backend - General helper utility functions
│   └── validate_pr.py          # PR validation script
├── .env                        # (Must be created manually) Stores API key and Telegram Token
├── .gitignore
├── README.md                   # This file
├── requirements.txt            # Python dependency list
├── run_pipeline.py             # One-click script to run the entire backend pipeline
└── run_set_webhook.py          # Entry point script to set the Telegram webhook
```

### Data Structure

The core output of the system is a JSON object containing two key parts: `nodes` and `relationships`.

**Nodes** are the entities in the graph. Their attributes include a unique `id` (preferably a Wikidata Q-Code), a `type`, a `name` (list of names), and `properties` (containing details like active periods, birth/death dates, and a brief description).

Predefined node types include: `Person`, `Organization`, `Movement`, `Event`, `Location`, `Document`.

**Relationships** define the connections between nodes. Their attributes include a `source` (source node id), a `target` (target node id), a `type` (relationship type), and `properties` (containing start/end times, positions, and supplementary descriptions). The relationship types are as follows:

| Type | Direction | Source Types | Target Types |
| :--- | :--- | :--- | :--- |
| `BORN_IN` | Directed | Person | Location |
| `ALUMNUS_OF` | Directed | Person | Organization |
| `MEMBER_OF` | Directed | Person, Organization | Organization |
| `CHILD_OF` | Directed | Person | Person |
| `SPOUSE_OF` | **Undirected** | Person | Person |
| `SIBLING_OF` | **Undirected** | Person | Person |
| `LOVER_OF` | **Undirected** | Person | Person |
| `RELATIVE_OF` | **Undirected** | Person | Person |
| `FRIEND_OF` | **Undirected** | Person, Organization | Person, Organization |
| `ENEMY_OF` | **Undirected** | Person, Organization | Person, Organization |
| `SUBORDINATE_OF` | Directed | Person, Organization | Person, Organization |
| `MET_WITH` | **Undirected** | Person | Person |
| `PUSHED` | Directed | Person, Organization, Event, Movement, Document | Person, Organization, Event, Movement, Document |
| `BLOCKED` | Directed | Person, Organization, Event, Movement, Document | Person, Organization, Event, Movement, Document |
| `INFLUENCED` | Directed | Person, Organization, Event, Movement, Document | Person, Organization, Event, Movement, Document, Location |
| `FOUNDED` | Directed | Person, Organization | Organization |

### Visualization

The project provides an interactive frontend visualization interface in the `docs/` directory. It uses PixiJS (WebGL) for high-performance rendering and D3.js for physics-based layouts, enabling the smooth display of thousands of nodes.

The interface supports filtering nodes and relationships by a time range and dynamically showing or hiding different types of nodes through a legend.

The page is deployed via GitHub Pages and can be accessed at [this link](https://anonym-g.github.io/Chinese-Elite/).

### How to Deploy

1.  Clone this repository to your local machine.
2.  Install all necessary Python dependencies: `pip install -r requirements.txt`.
3.  Create a `.env` file in the project root and set your `GOOGLE_API_KEY`.
4.  Fill `data/LIST.md` with the entity names you wish to process, organized by category.
5.  Run `python run_pipeline.py` to execute the complete backend data processing pipeline.
6.  To avoid browser restrictions on local file access, it's recommended to serve the directory with a simple local HTTP server (e.g., `python -m http.server`) and open the provided URL in your browser.

### Project Status

This project is currently in the **Development** phase.

### Disclaimer

The data presented by this tool is for informational and research purposes only. All information is automatically generated from public sources and may be subject to the inherent delays and potential inaccuracies of those sources. When using the data, it should be independently verified with the provided official links or other primary sources.

### Contributing

This is an open-source project, and contributions of all forms are highly welcome. Whether you are a developer, data scientist, or domain expert, you can help by improving the extraction scripts, fine-tuning the LLM prompts, expanding the seed list, enhancing the front-end visualization, or reporting data inaccuracies. Please feel free to fork this repository, open an issue, or submit a pull request.

To make it easier for the community to participate in maintenance, this project integrates an LLM-driven automated Pull Request (PR) processing system.

The system only reviews data-related PRs, meaning it exclusively processes modifications to two project files: corrections to the `docs/master_graph_qcode.json` file and additions of new entities to the `data/LIST.md` file.

When making such contributions, you must follow one core rule: **each Pull Request must only contain changes to a single file.** PRs with changes to multiple files will be ignored by this automated system.

Between 01:00 and 16:00 UTC, the system automatically conducts a PR review approximately every 30 minutes.

As long as a PR modifies only one of the two specified files, an AI reviewer will assess whether the changes are reasonable.

If the change is deemed meaningful, the PR will be automatically merged. Conversely, if it is judged to be meaningless or problematic, the PR will be automatically closed with an explanatory comment.

Additionally, because this project has a dedicated automated data update pipeline (which starts at 18:00 UTC and can run for up to 6 hours), if your data-related PR is not merged promptly on the same day, it may fall out of sync with the main repository's version.

Therefore, please ensure the branch on your fork used for submitting PRs is updated daily.

### Detail Explanation of Data Revisions

1. Edit the `docs/master_graph_qcode.json` file (the main data file). You can clone the repository, manually edit this file to fix incorrect node properties or relationships, and then submit a Pull Request.

Please note that there are common issues with LLM-based extraction. For example, it may generate weak, redundant relationships (e.g., generating both `BLOCKED` and `INFLUENCED`, where the latter can often be deleted), or it may sometimes reverse the source and target for directed relationships (like `INFLUENCED`). We especially welcome corrections for these types of data errors.

2. Adding to the seed list. Adding entity names to the first six categories in `data/LIST.md` will cause the script to retrieve the corresponding Wikipedia pages during its next run.

Note that due to how the script works, you must add the full Wikipedia page title. Here are two examples:

For the Central People's Government from 1949-1954, the Wikipedia page URL is [https://en.wikipedia.org/wiki/Central_People's_Government_of_the_People's_Republic_of_China_(1949–1954)](https://en.wikipedia.org/wiki/Central_People's_Government_of_the_People's_Republic_of_China_(1949–1954))

You would need to copy the full page title, "Central People's Government of the People's Republic of China (1949–1954)" (underscores can be replaced with spaces, but other parts must match the page title exactly, including hyphens and characters), and add it under the Organization category in the `data/LIST.md` file, with a "(en) " prefix.

For Chiang Kai-shek, the Wikipedia page URL is [https://en.wikipedia.org/wiki/Chiang_Kai-shek](https://en.wikipedia.org/wiki/Chiang_Kai-shek)

You would need to copy "Chiang Kai-shek", and add it under the Person category in the `data/LIST.md` file.

```data/LIST.md
Person
...
(en) Chiang Kai-shek

Organization
...
(en) Central People's Government of the People's Republic of China (1949–1954)

...

```

This way, when the GitHub Action Bot next runs `.github/workflows/update_data.yml`, it will automatically process these two pages and extract relevant information using the LLM—provided the LLM API usage has not exceeded its limit.

Additionally, when adding these terms, please use `Shift + F` to quickly check for duplicates. If the term is already in the first six categories, there is no need to add it. If the term is under the `new` category, please delete it (terms under `new` are not processed and act as a buffer pool).

```data/LIST.md
Person
...
(en) Chiang Kai-shek

...

new
...
~~(en) Chiang Kai-shek~~
...

```

### About Pull Requests

When contributing, it is crucial to understand how GitHub Pull Requests work.

A PR is a dynamic reference to a specific branch in your forked repository. This means that any new commits you push to that source branch *after* creating the PR will be automatically added to it, until the PR is either merged or closed.

This feature can lead to unexpected errors.

For example, if you submit a PR and then continue to make other changes on the same branch without waiting for it to be merged, those subsequent modifications, which may be incomplete or unrelated, will be appended to your original PR. This is very likely to cause the AI review to fail.

Therefore, we strongly recommend following this robust contribution workflow:

1.  Complete your changes to a **single file** in your forked repository.

2.  Commit and push to your fork, then open a Pull Request to the main repository.

3.  **Wait patiently.** Before your PR is automatically merged or closed, please do not push any new commits to the branch from which the PR was created.

4.  After your PR has been successfully merged, first synchronize your local repository with the latest state of the main repository (e.g., via `git pull origin main`).

5.  Once synchronization is complete, you may begin your next contribution.

Following this process ensures that each of your contributions is an independent, clean unit, guaranteeing that the automated system can process it smoothly.

You can further refer to the historical PR on the Pull Request page of this project：[https://github.com/anonym-g/Chinese-Elite/pulls?q=is%3Apr+is%3Aclosed](https://github.com/anonym-g/Chinese-Elite/pulls?q=is%3Apr+is%3Aclosed)
