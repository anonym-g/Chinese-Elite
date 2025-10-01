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
    - [技术实现](#技术实现)
    - [数据结构](#数据结构)
    - [可视化](#可视化)
    - [如何部署](#如何部署)
    - [数据清洗与人工手动审查](#数据清洗与人工手动审查)
    - [项目状态](#项目状态)
    - [免责声明](#免责声明)
    - [贡献](#贡献)
  - [English Version](#english-version)
    - [Project Introduction](#project-introduction)
    - [Technical Implementation](#technical-implementation)
    - [Data Structure](#data-structure)
    - [Visualization](#visualization)
    - [How to Deploy](#how-to-deploy)
    - [Data Cleaning and Manual Review](#data-cleaning-and-manual-review)
    - [Project Status](#project-status)
    - [Disclaimer](#disclaimer)
    - [Contributing](#contributing)

-----

## 中文版

### 项目简介

本项目利用大语言模型的力量，从维基百科等公开数据源中提取信息，创建一个可自我更新、公开可访问的中国政商精英关系图谱数据库，并提供可视化前端。核心目标，是为研究人员、记者、以及任何对理解中国权力结构感兴趣的人士，提供一个透明、可验证且持续改进的分析工具。

### 技术实现

**项目结构：**

```
.
├── .cache/                 # 存放缓存文件，如链接检查状态
├── .github/
│   └── workflows/
│       └── update_data.yml # GitHub Actions 自动化工作流
├── data/
│   ├── cleaned_data/       # 存放经手动审查和修正后的数据
│   ├── person/
│   │   └── ...             # 按类别存放的原始提取数据
│   ├── ...
│   ├── pageviews.log       # 页面访问频次检查日志
│   ├── processed_files.log # 记录已合并处理的文件名
│   └── LIST.txt            # 待处理的实体种子列表
├── data_to_be_cleaned/     # 存放由脚本分离出的问题数据
│   └── ...
├── docs/
│   ├── modules/
│   │   ├── config.js         # 前端-全局配置
│   │   ├── dataProcessor.js  # 前端-数据处理与分析
│   │   ├── graphView.js      # 前端-D3.js视图渲染
│   │   ├── state.js          # 前端-状态管理
│   │   ├── uiController.js   # 前端-UI控件与事件处理
│   │   └── utils.js          # 前端-辅助工具函数
│   ├── main.js               # 前端-主入口文件
│   ├── consolidated_graph.json # 合并后的主图谱文件
│   ├── index.html            # 可视化页面
│   └── style.css             # 页面样式
├── scripts/
│   ├── prompts/              # 存放LLM的系统提示(Prompt)文本
│   │   ├── merge_check.txt
│   │   ├── merge_execute.txt
│   │   └── parser_system.txt
│   ├── check_pageviews.py    # 检查实体热度并重排序列表
│   ├── clean_data.py         # 数据清洗脚本
│   ├── config.py             # 存放所有路径和模型配置
│   ├── merge_graphs.py       # 数据合并脚本
│   ├── parse_gemini.py       # LLM 解析脚本
│   ├── process_list.py       # 处理列表的核心脚本
│   └── utils.py              # 辅助工具脚本
├── .env                    # (需自行创建) 存放 API 密钥
├── .gitignore
├── README.md
├── requirements.txt        # Python 依赖列表
└── run_pipeline.py         # 完整流水线一键执行脚本
```

**后端数据处理流程** 始于用户在 `data/LIST.txt` 文件中定义的实体种子列表。

`scripts/process_list.py` 脚本会读取此列表，并通过维基百科API检查每个条目的最后修订时间，以避免对未更新的页面进行重复处理。

对于需要处理的条目，`scripts/utils.py` 会获取其Wikitext源码并进行简繁转换。随后，`scripts/parse_gemini.py` 脚本会将纯文本源码提交给大语言模型（目前使用Gemini-2.5-pro），以JSON格式提取出结构化的节点与关系信息。

当生成多个独立的JSON文件后，`scripts/merge_graphs.py` 脚本负责将所有碎片化的数据智能地合并到 `docs/consolidated_graph.json` 这个主图谱文件中。合并过程采用两阶段LLM调用以提高效率和准确性。

项目的所有关键配置，如模型名称、文件路径等，均集中在 `scripts/config.py` 文件中，方便用户统一管理和修改。

整个流程可以通过执行根目录的 `run_pipeline.py` 脚本一键启动。

该流水线脚本会按顺序执行实体处理、图谱合并、数据清洗，并最终调用 `scripts/check_pageviews.py` 检查各实体的维基百科页面热度，根据访问频次对 `data/LIST.txt` 列表进行重排序。

**前端可视化架构** 采用了模块化设计，以实现功能解耦和高可维护性。

`docs/main.js` 是前端应用的主入口，负责初始化和协调各个模块。核心逻辑分布在 `docs/modules/` 目录下：

`state.js` 管理应用的所有状态（如时间范围、选中节点）；

`dataProcessor.js` 负责加载、过滤和分析图谱数据；

`graphView.js` 封装了所有D3.js的渲染逻辑，负责将数据绘制成SVG图形；

`uiController.js` 则处理所有用户界面（如日期选择器、搜索框）的交互事件。

### 数据结构

系统输出的核心是JSON对象，包含 `nodes` (节点) 和 `relationships` (关系) 两个关键部分。

**节点** `nodes` 是图中的实体，其属性包括唯一的 `id` (通常是实体在Wikipedia的主页面名称)、`type` (实体类型，如人物、组织、运动、事件、地点、文件等六种预设类型)、`aliases` (别名列表) 以及 `properties` (包含活跃时期、生卒年月、地理位置、简短描述等详细信息)。

**关系** `relationships` 定义了节点之间的连接，其属性包括 `source` (源节点id)、`target` (目标节点id)、`type` (关系类型) 以及 `properties` (包含关系起止时间、职位、补充描述等)。目前，关系类型包括以下几种：

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
| `FOUNDED` (创立) | 有向 | 人物, 组织 | 组织, 运动 |

### 可视化

项目在 `docs/` 目录下提供了一个基于 D3.js 构建的交互式前端可视化界面。用户通过浏览器打开 `docs/index.html` 文件，即可加载并浏览 `docs/consolidated_graph.json` 中的图谱数据。

该界面支持按时间范围筛选节点和关系，通过图例动态显示或隐藏不同类型的节点，并允许用户通过点击节点来高亮其直接关联网络。

节点的尺寸与其在当前时间范围内的度（连接数）相关联，提供了直观的重要性参考。

该页面亦基于 GitHub Pages 部署在云端，允许用户通过[链接](https://anonym-g.github.io/Chinese-Elite/)进行访问。

### 如何部署

1.  克隆本仓库至本地。
2.  安装所有必要的Python依赖包：`pip install -r requirements.txt`。
3.  在项目根目录创建 `.env` 文件，并设置你的 `GOOGLE_API_KEY`。
4.  在 `data/LIST.txt` 中按分类填入你希望抓取的实体名称。
5.  执行项目根目录下的 `python run_pipeline.py` 脚本。该脚本将自动按顺序完成实体列表处理、图谱合并、数据清洗及列表重排序的全过程。
6.  在浏览器中打开 `docs/index.html` 文件即可查看和交互。为避免浏览器本地文件访问限制，建议通过一个简单的本地HTTP服务器来访问该文件。

### 数据清洗与人工手动审查

为保证主图谱的长期健康，可以定期运行 `scripts/clean_data.py` 脚本。

它会遍历主图谱中的所有节点，检查其对应的维基百科链接状态，并将重定向、指向消歧义页或链接失效的“问题节点”及其相关关系一并分离，存入项目根目录下的 `data_to_be_cleaned/` 文件夹中，以供手动审查或修正。

该脚本内置缓存机制（缓存位于 `.cache/` 目录），能够记录已检查过的链接状态，避免重复发起网络请求，从而显著提升后续运行的效率。

手动审查完毕并修正后的数据文件，可以移入 `data/cleaned_data/` 文件夹进行归档，或直接将修正后的内容并入主图谱文件 `docs/consolidated_graph.json`。

### 项目状态

本项目目前处于**开发**阶段。

### 免责声明

本工具呈现的数据仅供参考与研究之用。所有信息均由程序自动从公开来源生成，并可能受到源数据固有的延迟和潜在不准确性影响。在使用时，应通过数据中提供的官方链接或其他一手资料进行独立核实。

### 贡献

这是一个开源项目，非常欢迎任何形式的贡献。无论你是开发者、数据科学家还是领域专家，都可以通过改进提取脚本、优化LLM提示、扩展种子列表、增强前端可视化或报告数据不准确之处来提供帮助。请自由Fork本仓库、开启Issue或提交Pull Request。

-----

## English Version

### Project Introduction

This project leverages the power of Large Language Models to extract information from public data sources like Wikipedia, creating a self-updating, publicly accessible graph database of relationship networks among China's political and business elites, complete with a visualization front-end. The core objective is to provide a transparent, verifiable, and continuously improving analytical tool for researchers, journalists, and anyone interested in understanding China's power structures.

### Technical Implementation

**Project Structure:**

```
.
├── .cache/                 # Stores cache files, e.g., link status checks
├── .github/
│   └── workflows/
│       └── update_data.yml # GitHub Actions workflow for automation
├── data/
│   ├── cleaned_data/       # Stores data that has been manually reviewed and corrected
│   ├── person/
│   │   └── ...             # Stores raw extracted data, categorized
│   ├── ...
│   ├── pageviews.log       # Log file for page view checks
│   ├── processed_files.log # Logs filenames that have been merged
│   └── LIST.txt            # Seed list of entities to be processed
├── data_to_be_cleaned/     # Stores problematic data separated by the cleaning script
│   └── ...
├── docs/
│   ├── modules/
│   │   ├── config.js         # Frontend - Global configuration
│   │   ├── dataProcessor.js  # Frontend - Data processing and analysis
│   │   ├── graphView.js      # Frontend - D3.js view rendering
│   │   ├── state.js          # Frontend - State management
│   │   ├── uiController.js   # Frontend - UI controls and event handling
│   │   └── utils.js          # Frontend - Utility functions
│   ├── main.js               # Frontend - Main entry point
│   ├── consolidated_graph.json # The main, merged graph file
│   ├── index.html            # Visualization page
│   └── style.css             # Page stylesheet
├── scripts/
│   ├── prompts/              # Stores system prompt text files for LLMs
│   │   ├── merge_check.txt
│   │   ├── merge_execute.txt
│   │   └── parser_system.txt
│   ├── check_pageviews.py    # Checks entity popularity and reorders the list
│   ├── clean_data.py         # Data cleaning script
│   ├── config.py             # Stores all path and model configurations
│   ├── merge_graphs.py       # Data merging script
│   ├── parse_gemini.py       # LLM parsing script
│   ├── process_list.py       # Core script for processing the list
│   └── utils.py              # Utility script
├── .env                    # (Must be created manually) Stores API key
├── .gitignore
├── README.md
├── requirements.txt        # Python dependency list
└── run_pipeline.py         # One-click script to run the entire pipeline
```

The **backend data processing workflow** begins with a user-defined seed list of entities in the `data/LIST.txt` file. 

The `scripts/process_list.py` script reads this list and checks the last revision time of each entry via the Wikipedia API to avoid reprocessing unchanged pages. 

For entries that need processing, `scripts/utils.py` fetches their Wikitext source. 

Subsequently, `scripts/parse_gemini.py` submits the plain text to an LLM (currently using Gemini-2.5-pro) to extract structured nodes and relationships in JSON format.

Once multiple individual JSON files are generated, `scripts/merge_graphs.py` intelligently consolidates all the fragmented data into the main graph file, `docs/consolidated_graph.json`, using a two-stage LLM call to improve efficiency and accuracy.

All key project configurations are centralized in `scripts/config.py`. 

The entire workflow can be initiated by executing `run_pipeline.py`, which sequentially runs entity processing, graph merging, data cleaning, and finally calls `scripts/check_pageviews.py` to check the Wikipedia page popularity of each entity and reorder the `data/LIST.txt` file based on view counts.

The **frontend visualization architecture** is modular for better maintainability. 

`docs/main.js` serves as the main entry point, initializing and coordinating all modules. The core logic is split into files under `docs/modules/`: 

`state.js` manages all application state (e.g., time range, selected node); 

`dataProcessor.js` is responsible for loading, filtering, and analyzing the graph data; 

`graphView.js` encapsulates all D3.js rendering logic to draw the data as an SVG; and 

`uiController.js` handles all user interface interactions (e.g., date pickers, search box).

### Data Structure

The core output of the system is a JSON object containing two key parts: `nodes` and `relationships`.

**Nodes** are the entities in the graph. Their attributes include a unique `id` (usually the entity's main page name on Wikipedia), a `type` (one of six predefined types such as Person, Organization, Movement), a list of `aliases`, and `properties` (containing details like active period, birth/death dates, location, and a brief description).

**Relationships** define the connections between nodes. Their attributes include a `source` (source node id), a `target` (target node id), a `type` (one of predefined types), and `properties` (containing start/end times, positions, and supplementary descriptions). Relationship types currently includes:

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
| `FOUNDED` | Directed | Person, Organization | Organization, Movement |

### Visualization

The project provides an interactive front-end visualization interface built with D3.js, located in the `docs/` directory. By opening the `docs/index.html` file in a browser, users can load and browse the graph data from `docs/consolidated_graph.json`.

The interface supports filtering nodes and relationships by a time range, dynamically showing or hiding different types of nodes through a legend, and allowing users to highlight a node's direct network by clicking on it.

The size of a node correlates with its degree (number of connections) within the current time frame, offering an intuitive reference for its importance.

The page is also deployed in the cloud based on GitHub Pages, which can be accessed by users via [link](https://anonym-g.github.io/Chinese-Elite/).

### How to Deploy

1.  Clone this repository to your local machine.
2.  Install all necessary Python dependencies: `pip install -r requirements.txt`.
3.  Create a `.env` file in the project root and set your `GOOGLE_API_KEY`.
4.  Fill in the entity names you wish to crawl in `data/LIST.txt`, categorized accordingly.
5.  Execute the `python run_pipeline.py` script in the project root. This script will automatically handle the entire process of entity list processing, graph merging, data cleaning, and list reordering.
6.  Open the `docs/index.html` file in a browser to view and interact with the data. To avoid local file access restrictions in browsers, it is recommended to access this file through a simple local HTTP server.

### Data Cleaning and Manual Review

To ensure the long-term health of the main graph, the `scripts/clean_data.py` script can be run periodically. 

It iterates through all nodes in the main graph, checks their corresponding Wikipedia link status, and separates "problem nodes"—those that are redirects, point to disambiguation pages, or have broken links—along with their associated relationships. 

These are saved into the `data_to_be_cleaned/` directory at the project root for manual review and correction. 

The script has a built-in caching mechanism (cache located in the `.cache/` directory) to remember the status of checked links, avoiding repeated network requests and significantly speeding up subsequent runs.

After manual review and correction, the data files can be moved to the `data/cleaned_data/` directory for archival, or their corrected content can be merged directly into the main graph file, `docs/consolidated_graph.json`.

### Project Status

This project is currently in the **Development** phase.

### Disclaimer

The data presented by this tool is for informational and research purposes only. All information is automatically generated from public sources and may be subject to the inherent delays and potential inaccuracies of those sources. When using the data, it should be independently verified with the provided official links or other primary sources.

### Contributing

This is an open-source project, and highly welcome contributions of all forms. Whether you are a developer, data scientist, or domain expert, you can help by improving the extraction scripts, fine-tuning the LLM prompts, expanding the seed list, enhancing the front-end visualization, or reporting data inaccuracies. Please feel free to fork this repository, open an issue, or submit a pull request.