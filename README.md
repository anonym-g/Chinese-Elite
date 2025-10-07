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
  - [English Version](#english-version)
    - [Project Introduction](#project-introduction)
    - [Project Structure](#project-structure)
    - [Data Structure](#data-structure)
    - [Visualization](#visualization)
    - [How to Deploy](#how-to-deploy)
    - [Project Status](#project-status)
    - [Disclaimer](#disclaimer)
    - [Contributing](#contributing)

-----

## 中文版

### 项目简介

本项目利用大语言模型的力量，从维基百科等公开数据源中提取信息，创建一个可自我更新、公开可访问的中国政商精英关系图谱数据库，并提供可视化前端。核心目标，是为研究人员、记者、以及任何对理解中国权力结构感兴趣的人士，提供一个透明、可验证且持续改进的分析工具。

### 项目结构

```
.
├── .cache/                   # 存放缓存文件 (Q-Code、链接状态、页面热度)
├── .github/
│   └── workflows/
│       └── update_data.yml   # GitHub Actions 自动化数据更新工作流
├── data/
│   ├── person/
│   │   └── ...               # 按类别存放LLM原始提取的JSON数据
│   ├── ...
│   ├── processed_files.log   # 记录已合并处理的文件名
│   └── LIST.txt              # 待处理的实体种子列表 (按热度排序)
├── data_to_be_cleaned/       # 存放由脚本分离出的待手动清理的数据
├── docs/
│   ├── data/
│   │   ├── nodes/            # 按需加载的“简单数据库”
│   │   │   └── Q12345/
│   │   │       ├── node.json
│   │   │       └──...
│   │   ├── initial.json      # 前端首次加载的核心图谱 (暂未使用)
│   │   └── name_to_id.json   # 全局搜索的名称-ID映射索引
│   ├── modules/
│   │   ├── config.js         # 前端配置
│   │   ├── dataProcessor.js  # 前端-数据处理、按需加载与路径查找
│   │   ├── graphView.js      # 前端-PixiJS (WebGL) 视图渲染与交互
│   │   ├── state.js          # 前端-状态管理
│   │   ├── uiController.js   # 前端-UI控件与事件处理
│   │   └── utils.js          # 前端-辅助工具函数
│   ├── main.js               # 前端-主入口文件
│   ├── master_graph_qcode.json # 合并后的完整主图谱文件
│   ├── index.html            # 可视化页面
│   └── style.css             # 页面样式
├── logs/                     # 存放后端运行日志
├── scripts/
│   ├── prompts/              # 存放LLM的提示词 (Prompt) 文本
│   │   └── ...
│   ├── api_rate_limiter.py   # API速率限制器
│   ├── check_pageviews.py    # 检查实体热度并重排序列表
│   ├── clean_data.py         # 深度数据清洗与维护脚本
│   ├── config.py             # 后端-所有路径和模型配置
│   ├── generate_frontend_data.py # 生成所有前端数据文件
│   ├── merge_graphs.py       # 增量数据智能合并脚本
│   ├── parse_gemini.py       # LLM 解析脚本
│   ├── process_list.py       # 根据列表处理实体的核心脚本
│   └── utils.py              # 维基百科API客户端与辅助工具
├── .env                      # (需自行创建) 存放 API 密钥
├── .gitignore
├── README.md
├── requirements.txt          # Python 依赖列表
└── run_pipeline.py           # 完整流水线一键执行脚本
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
4.  在 `data/LIST.txt` 中按分类填入你希望抓取的实体名称。
5.  执行 `python run_pipeline.py` 脚本，它将自动完成完整的后端数据处理流程。
6.  为避免浏览器本地文件访问限制，建议通过一个简单的本地HTTP服务器（如 `python -m http.server`）启动服务，然后在浏览器中访问。

### 项目状态

本项目目前处于**开发**阶段。

### 免责声明

本工具呈现的数据仅供参考与研究之用。所有信息均由程序自动从公开来源生成，并可能受到源数据固有的延迟和潜在不准确性影响。在使用时，应通过数据中提供的官方链接或其他一手资料进行独立核实。

### 贡献

这是一个开源项目，非常欢迎任何形式的贡献。无论你是开发者、数据科学家还是领域专家，都可以通过改进提取脚本、优化LLM提示、扩展种子列表、增强前端可视化或报告数据不准确之处来提供帮助。请自由Fork本仓库、开启Issue或提交Pull Request。

如果您希望修正数据，主要有两种方式。

最直接的方式是直接修改 `docs/master_graph_qcode.json` 文件 (主数据文件) 。您可以克隆仓库，手动编辑此文件以修正错误的节点属性或关系，然后提交一个 Pull Request。

请注意，LLM在提取数据时存在一些普遍问题，例如：它可能会生成一些较弱的冗余关系（如同时生成 `BLOCKED`、`INFLUENCED`，后者通常可直接删去）；对于部分有向关系（如 `INFLUENCED`），它有时会弄反源节点和目标节点。我们尤其欢迎针对这类数据错误的修正。

另一种方式是通过添加种子列表来影响数据。在 `data/LIST.txt` 的前6个栏目中添加实体名称，可以让脚本在后续运行时检索对应的维基百科页面。

注意，考虑到脚本的工作方式，您必须添加完整的维基百科页面名。以下举两例说明：

49-54年的中央人民政府，维基百科的页面链接是 [https://zh.wikipedia.org/wiki/中华人民共和国中央人民政府_(1949年—1954年)](https://zh.wikipedia.org/wiki/中华人民共和国中央人民政府_(1949年—1954年))

那么，您需要复制粘贴完整的页面名，"中华人民共和国中央人民政府 (1949年—1954年)" (下划线可以正常换成空格，其他部分必须与页面名一致，包括破折线、两个"年"字)，并将其添加到 `data/LIST.txt` 文件的 Organization (组织) 条目下。

蒋介石，维基百科的页面链接是 [https://zh.wikipedia.org/wiki/蔣中正](https://zh.wikipedia.org/wiki/蔣中正)

那么，您需要复制粘贴 "蔣中正" (繁体"蔣"，以便查重)，并将其添加到 `data/LIST.txt` 文件的 Person (人物) 条目下。

`data/LIST.txt`: 
Person
...
蔣中正

Organization
...
中华人民共和国中央人民政府 (1949年—1954年)

...

这样，GitHub Action Bot在下次运行 `.github/workflows/update_data.yml` 时，将自动处理这两个页面，利用LLM提取其中的有关信息。——如果LLM API用量没有超限的话。

另外，在添加这些词项时，请用 `Shift + F` 随手查一下重。若词项已经在前六条，则不必添加。若条目在 new 条目下，请随手删除 (new 下面的词条不会被处理，相当于一个缓冲池) 。

`data/LIST.txt`: 
Person
...
蔣中正

...

new
...
~~蔣中正~~
...

-----

## English Version

### Project Introduction

This project leverages the power of Large Language Models to extract information from public data sources like Wikipedia, creating a self-updating, publicly accessible graph database of relationship networks among China's political and business elites, complete with a visualization front-end. The core objective is to provide a transparent, verifiable, and continuously improving analytical tool for researchers, journalists, and anyone interested in understanding China's power structures.

### Project Structure

```
.
├── .cache/                   # Stores cache files (Q-Codes, link status, page views)
├── .github/
│   └── workflows/
│       └── update_data.yml   # GitHub Actions workflow for automated data updates
├── data/
│   ├── person/
│   │   └── ...               # Raw JSON data extracted by LLM, categorized
│   ├── ...
│   ├── processed_files.log   # Logs filenames that have been merged
│   └── LIST.txt              # Seed list of entities to be processed (sorted by popularity)
├── data_to_be_cleaned/       # Stores data separated by scripts for manual cleaning
├── docs/
│   ├── data/
│   │   ├── nodes/            # "Simple database" for on-demand loading
│   │   │   └── Q12345/
│   │   │       ├── node.json
│   │   │       └── ...
│   │   ├── initial.json      # Core graph for initial frontend load (currently unused)
│   │   └── name_to_id.json   # Name-to-ID mapping index for global search
│   ├── modules/
│   │   ├── config.js         # Frontend configuration
│   │   ├── dataProcessor.js  # Frontend - Data processing, on-demand loading, and pathfinding
│   │   ├── graphView.js      # Frontend - PixiJS (WebGL) view rendering and interaction
│   │   ├── state.js          # Frontend - State management
│   │   ├── uiController.js   # Frontend - UI controls and event handling
│   │   └── utils.js          # Frontend - Utility functions
│   ├── main.js               # Frontend - Main entry point
│   ├── master_graph_qcode.json # The final, complete master graph file
│   ├── index.html            # Visualization page
│   └── style.css             # Page stylesheet
├── logs/                     # Stores backend runtime logs
├── scripts/
│   ├── prompts/              # Stores prompt text files for the LLM
│   │   └── ...
│   ├── api_rate_limiter.py   # API rate limiter
│   ├── check_pageviews.py    # Checks entity popularity and reorders the list
│   ├── clean_data.py         # Deep data cleaning and maintenance script
│   ├── config.py             # Backend - Configuration for all paths and models
│   ├── generate_frontend_data.py # Generates all frontend data files
│   ├── merge_graphs.py       # Script for intelligent incremental data merging
│   ├── parse_gemini.py       # LLM parsing script
│   ├── process_list.py       # Core script for processing entities from the list
│   └── utils.py              # Wikipedia API client and helper utilities
├── .env                      # (Must be created manually) Stores API key
├── .gitignore
├── README.md
├── requirements.txt          # Python dependency list
└── run_pipeline.py           # One-click script to run the entire pipeline
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
4.  Fill `data/LIST.txt` with the entity names you wish to process, organized by category.
5.  Run `python run_pipeline.py` to execute the complete backend data processing pipeline.
6.  To avoid browser restrictions on local file access, it's recommended to serve the directory with a simple local HTTP server (e.g., `python -m http.server`) and open the provided URL in your browser.

### Project Status

This project is currently in the **Development** phase.

### Disclaimer

The data presented by this tool is for informational and research purposes only. All information is automatically generated from public sources and may be subject to the inherent delays and potential inaccuracies of those sources. When using the data, it should be independently verified with the provided official links or other primary sources.

### Contributing

This is an open-source project, and contributions of all forms are highly welcome. Whether you are a developer, data scientist, or domain expert, you can help by improving the extraction scripts, fine-tuning the LLM prompts, expanding the seed list, enhancing the front-end visualization, or reporting data inaccuracies. Please feel free to fork this repository, open an issue, or submit a pull request.

If you wish to correct data, there are two primary methods.

The most direct way is to edit the `docs/master_graph_qcode.json` file (the main data file). You can clone the repository, manually edit this file to fix incorrect node properties or relationships, and then submit a Pull Request.

Please note that there are common issues with LLM-based extraction. For example, it may generate weak, redundant relationships (e.g., generating both `BLOCKED` and `INFLUENCED`, where the latter can often be deleted), or it may sometimes reverse the source and target for directed relationships (like `INFLUENCED`). We especially welcome corrections for these types of data errors.

Another way is to influence the data by adding to the seed list. Adding entity names to the first six categories in `data/LIST.txt` will cause the script to retrieve the corresponding Wikipedia pages during its next run.

Note that due to how the script works, you must add the full Wikipedia page title. Here are two examples:

For the Central People's Government from 1949-1954, the Wikipedia page URL is https://zh.wikipedia.org/wiki/中华人民共和国中央人民政府_(1949年—1954年)

You would need to copy the full page title, "中华人民共和国中央人民政府 (1949年—1954年)" (underscores can be replaced with spaces, but other parts must match the page title exactly, including hyphens and characters), and add it under the Organization category in the `data/LIST.txt` file.

For Chiang Kai-shek, the Wikipedia page URL is https://zh.wikipedia.org/wiki/蔣中正

You would need to copy "蔣中正" (in Traditional Chinese) and add it under the Person category in the `data/LIST.txt` file.

`data/LIST.txt`:
Person
...
蔣中正

Organization
...
中华人民共和国中央人民政府 (1949年—1954年)

...

This way, when the GitHub Action Bot next runs `.github/workflows/update_data.yml`, it will automatically process these two pages and extract relevant information using the LLM—provided the LLM API usage has not exceeded its limit.

Additionally, when adding these terms, please use `Shift + F` to quickly check for duplicates. If the term is already in the first six categories, there is no need to add it. If the term is under the `new` category, please delete it (terms under `new` are not processed and act as a buffer pool).

`data/LIST.txt`:
Person
...
蔣中正

...

new
...
~~蔣中正~~
...