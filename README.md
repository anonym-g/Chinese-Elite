# Chinese Elite (中国精英)

一个实验性项目，基于LLMs (大语言模型) 解析公共数据、并与官方来源交叉引用，自动绘制中国精英的关系网络。

点击 [这里](https://anonym-g.github.io/Chinese-Elite) 开始体验。

An experimental project, that automatically maps the relationship networks of Chinese Elites by parsing public data using LLMs and cross-referencing with official sources.

Click [Here](https://anonym-g.github.io/Chinese-Elite) to begin.

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
├── .github/
│   └── workflows/
│       └── update_data.yml      # (有待实现) GitHub Actions 自动化工作流
├── data/
│   ├── cleaned_data/            # 存放经手动审查和修正后的数据
│   ├── person/
│   │   └── ...                  # 按类别存放的原始提取数据
│   ├── ...
│   ├── consolidated_graph.json  # 合并后的主图谱文件
│   └── LIST.txt                 # 待处理的实体种子列表
├── data_to_be_cleaned/          # 存放由脚本分离出的问题数据
│   ├── no_page_YYYY-MM-DD-HH-MM-SS.json
│   ├── redirect_YYYY-MM-DD-HH-MM-SS.json
│   └── disambig_YYYY-MM-DD-HH-MM-SS.json
├── docs/
│   ├── app.js                   # D3.js 可视化逻辑
│   ├── index.html               # 可视化页面
│   └── style.css                # 页面样式
├── scripts/
│   ├── clean_data.py            # 数据清洗脚本
│   ├── merge_graphs.py          # 数据合并脚本
│   ├── parse_gemini.py          # LLM 解析脚本
│   ├── process_list.py          # 主处理流程脚本
│   ├── utils.py                 # 辅助工具脚本
│   └── main.py                  # 主执行文件
├── .env                         # (需自行创建) 存放 API 密钥
├── .gitignore
├── README.md
└── requirements.txt             # Python 依赖列表
```

首先，用户需在 `data/LIST.txt` 文件中定义一个包含实体（人物、组织、事件等）名称的种子列表。执行 `scripts/process_list.py` 脚本后，系统会读取此列表，并通过维基百科API检查每个条目的最后修订时间，以避免对未更新的页面进行重复处理。

对于需要处理的条目，`scripts/utils.py` 会获取其Wikitext源码并进行简繁转换。

随后，`scripts/parse_gemini.py` 脚本会将纯文本源码提交给大语言模型（目前使用Gemini-2.5-pro）。通过一个包含详细指令、输出格式定义的系统提示（System Prompt），以及包含少量样本（Few-shot Examples）的用户输入，模型被引导提取出结构化的节点与关系信息，并以JSON格式返回。每个处理过的条目都会在 `data/` 目录下生成一个独立的JSON文件。

当生成多个独立的JSON文件后，运行 `scripts/merge_graphs.py` 脚本。该脚本负责将所有碎片化的数据智能地合并到 `data/consolidated_graph.json` 这个主图谱文件中。

为了提高效率和准确性，合并过程采用两阶段LLM调用：

首先使用一个轻量级模型 (Gemma-3-27b-it) 预检新旧节点数据，判断是否存在有价值的新信息；如果确认需要合并，再调用一个性能更强的模型 (Gemini-2.5-flash) ，根据上下文逻辑，将新旧信息融合成一个完整版本。此脚本同时会处理别名映射，尽可能确保实体ID的一致性。

### 数据结构

系统输出的核心是JSON对象，包含 `nodes` (节点) 和 `relationships` (关系) 两个关键部分。

**节点** `nodes` 是图中的实体，其属性包括唯一的 `id` (通常是实体在Wikipedia的主页面名称)、`type` (实体类型，如人物、组织、运动等六种预设类型)、`aliases` (别名列表) 以及 `properties` (包含活跃时期、生卒年月、地理位置、简短描述等详细信息)。

**关系** `relationships` 定义了节点之间的连接，其属性包括 `source` (源节点id)、`target` (目标节点id)、`type` (关系类型，如上级/下级、成员、推动/阻碍等二十种预设类型) 以及 `properties` (包含关系起止时间、职位、补充描述等)。

这种结构化的设计旨在为复杂的网络分析提供坚实基础。

### 可视化

项目在 `docs/` 目录下提供了一个基于 D3.js 构建的交互式前端可视化界面。用户通过浏览器打开 `docs/index.html` 文件，即可加载并浏览 `data/consolidated_graph.json` 中的图谱数据。

该界面支持按时间范围筛选节点和关系，通过图例动态显示或隐藏不同类型的节点，并允许用户通过点击节点来高亮其直接关联网络。

节点的尺寸与其在当前时间范围内的度（连接数）相关联，提供了直观的重要性参考。

### 如何部署

1.  克隆本仓库至本地。
2.  安装所有必要的Python依赖包：`pip install -r requirements.txt`。
3.  在项目根目录创建 `.env` 文件，并设置你的 `GOOGLE_API_KEY`。
4.  在 `data/LIST.txt` 中按分类填入你希望抓取的实体名称。
5.  依次执行数据处理脚本：`python scripts/process_list.py`，然后 `python scripts/merge_graphs.py`。
6.  在浏览器中打开 `docs/index.html` 文件即可查看和交互。为避免浏览器本地文件访问限制，建议通过一个简单的本地HTTP服务器来访问该文件。

### 数据清洗与人工手动审查

为保证主图谱的长期健康，可以定期运行 `scripts/clean_data.py` 脚本。它会遍历主图谱中的所有节点，检查其对应的维基百科链接状态，并将重定向、指向消歧义页或链接失效的“问题节点”及其相关关系一并分离，存入项目根目录下的 `data_to_be_cleaned/` 文件夹中，以供手动审查或修正。该脚本内置缓存机制，以加速重复检查。

手动审查完毕并修正后的数据文件，可以移入 `data/cleaned_data/` 文件夹进行归档，或直接将修正后的内容并入主图谱文件 `data/consolidated_graph.json`。

关于手动审查的说明：

1.  **重定向类节点**：

    由于中文版Wikipedia有简繁差异，部分节点id字符正确、但页面链接为繁体索引（如“蒋中正”，wiki链接为[https://zh.wikipedia.org/zh-cn/蔣中正](https://zh.wikipedia.org/zh-cn/蔣中正)），这类重定向会被脚本自动过滤、不予分离。

    其它id的重定向又分两类：

    一是说法与wiki页正式名称不符的（如“中苏论战”，没有单独页面，重定向至wiki链接[https://zh.wikipedia.org/zh-cn/九评苏共](https://zh.wikipedia.org/zh-cn/九评苏共)）；

    二是因历史原因已弃用、没有单独页面的（如“中国共产党中央军事部”，属于中央军事委员会的机构沿革，重定向至[https://zh.wikipedia.org/zh-cn/中央军事委员会](https://zh.wikipedia.org/zh-cn/中央军事委员会)）。

    对于前者，可以手动更正，并将常见但不可用的id名移至别名数组"aliases"中。

    对于后者，可以添加属性"verified\_node"，赋值为"true"，以绕过二次检查。

    更改后，建议同时搜索调整relationship中使用该id的项，但也可以不调（前端渲染时，会自动移除端点不存在的冗余关系）。

    最后并入主图谱文件 `data/consolidated_graph.json` 。

    例：

    ```sample1.json
    // 将"中苏论战"移至"aliases"数组，并将id改为"九评苏共"
        {
          "id": "九评苏共",
          "type": "Event",
          "aliases": [
            "中苏论战"
          ],
          "properties": {
            "period": "1960 - 1969",
            "location": "中国、苏联",
            "description": "中苏两党在意识形态上的公开论战，是文革发动的背景之一，并导致中苏关系破裂。"
          }
        },
    ```

    ```sample2.json
    // 添加 "verified_node"
        {
          "id": "中国共产党中央军事部",
          "type": "Organization",
          "aliases": [
            "中共中央军事部",
            "中央军事部"
          ],
          "properties": {
            "period": [
              "1925-12-12 - 1926-11",
              "1928-7 - 1930-2"
            ],
            "description": "1925年12月12日，中共中央发布通告，将军事运动委员会改为中央军事部。1926年11月上旬，改为中央军事委员会。1928年7月，正式恢复设立中央军事部。1930年2月，军事部与军委合二为一。",
            "verified_node": true
          }
        },
    ```

2.  **消歧义类节点**：

    请手动搜索wiki、找到正确的消歧义项，并更改id。

    如"中央人民政府"，正确id是"中华人民共和国中央人民政府 (1949年—1954年)"。——注意用英文括号、单破折号、要带“年”字。

    例：

    ```sample3.json
    // "中央人民政府" -> "中华人民共和国中央人民政府 (1949年—1954年)"
        {
          "id": "中华人民共和国中央人民政府 (1949年—1954年)",
          "type": "Organization",
          "aliases": [
            "中华人民共和国中央人民政府",
            "中国中央人民政府",
            "中华人民共和国中央人民政府 (1949年-1954年)",
            "中华人民共和国中央人民政府 (1949—1954)",
            "中华人民共和国中央人民政府 (1949-1954)",
            "中央人民政府",
            "中央人民政府委员会"
          ],
          "properties": {
            "period": "1949-10-01 - 1954-09-27",
            "location": "北京市",
            "description": "1949年至1954年间、中华人民共和国成立初期的中央政府，是行使国家政权的最高机关。"
          }
        },
    ```

3.  **无链接类节点**：

    此类节点，大体分三类：

    一是wiki未能正确重定向。如"s:中华人民共和国宪法"，系LLM输出错误。

    二是文件类节点 (Document)，如"中华人民共和国国务院组织法"，在Wikisource有存档 (Wikipedia页面被删去，以避免重复)。

    三是重要性低、不存在wiki页面，但实体属实。如"程宜芝"，刘伯承的第一任妻子，wiki没有单独页面。又如"石家庄日报"，wiki没有单独页面。

    对于其一，可以修正后并入主图谱文件。

    对于二三，可以增添属性"verified\_node"，赋值为"true"。

### 项目状态

本项目目前处于**开发**阶段。

### 免责声明

本工具呈现的数据仅供参考与研究之用。所有信息均由程序自动从公开来源生成，并可能受到源数据固有的延迟和潜在不准确性影响。在使用时，应通过数据中提供的官方链接或其他一手资料进行独立核实。

### 贡献

这是一个开源项目，我们非常欢迎任何形式的贡献。无论你是开发者、数据科学家还是领域专家，都可以通过改进提取脚本、优化LLM提示、扩展种子列表、增强前端可视化或报告数据不准确之处来帮助我们。请自由Fork本仓库、开启Issue或提交Pull Request。

-----

## English Version

### Project Introduction

This project leverages the power of Large Language Models to extract information from public data sources like Wikipedia, creating a self-updating, publicly accessible graph database of relationship networks among China's political and business elites, complete with a visualization front-end. The core objective is to provide a transparent, verifiable, and continuously improving analytical tool for researchers, journalists, and anyone interested in understanding China's power structures.

### Technical Implementation

**Project Structure:**

```
.
├── .github/
│   └── workflows/
│       └── update_data.yml      # (To be implemented) GitHub Actions workflow for automation
├── data/
│   ├── cleaned_data/            # Stores data that has been manually reviewed and corrected
│   ├── person/
│   │   └── ...                  # Stores raw extracted data, categorized
│   ├── ...
│   ├── consolidated_graph.json  # The main, merged graph file
│   └── LIST.txt                 # Seed list of entities to be processed
├── data_to_be_cleaned/          # Stores problematic data separated by the cleaning script
│   ├── no_page_YYYY-MM-DD-HH-MM-SS.json
│   ├── redirect_YYYY-MM-DD-HH-MM-SS.json
│   └── disambig_YYYY-MM-DD-HH-MM-SS.json
├── docs/
│   ├── app.js                   # D3.js visualization logic
│   ├── index.html               # Visualization page
│   └── style.css                # Page stylesheet
├── scripts/
│   ├── clean_data.py            # Data cleaning script
│   ├── merge_graphs.py          # Data merging script
│   ├── parse_gemini.py          # LLM parsing script
│   ├── process_list.py          # Main processing workflow script
│   ├── utils.py                 # Utility script
│   └── main.py                  # Main executable file
├── .env                         # (Must be created manually) Stores API key
├── .gitignore
├── README.md
└── requirements.txt             # Python dependency list
```

First, a user defines a seed list of entity names (people, organizations, events, etc.) in the `data/LIST.txt` file. Executing the `scripts/process_list.py` script reads this list and checks the last revision time of each entry via the Wikipedia API to avoid reprocessing unchanged pages.

For entries that need processing, `scripts/utils.py` fetches their Wikitext source and performs Simplified/Traditional Chinese conversion.

Subsequently, the `scripts/parse_gemini.py` script submits the plain text to an LLM (currently using Gemini-2.5-pro). Guided by a System Prompt containing detailed instructions and output format definitions, along with user input containing few-shot examples, the model is directed to extract structured nodes and relationships, returning them in JSON format. Each processed entry generates a separate JSON file in a categorized subdirectory under `data/`.

Once multiple individual JSON files are generated, running the `scripts/merge_graphs.py` script intelligently consolidates all the fragmented data into the main graph file, `data/consolidated_graph.json`.

To improve efficiency and accuracy, the merging process employs a two-stage LLM call:

First, a lightweight model (Gemma-3-27b-it) pre-checks the new and existing node data to determine if there is valuable new information. If a merge is confirmed to be necessary, a more powerful model (Gemini-2.5-flash) is invoked to logically fuse the old and new information into a comprehensive version. This script also handles alias mapping to ensure the consistency of entity IDs as much as possible.

### Data Structure

The core output of the system is a JSON object containing two key parts: `nodes` and `relationships`.

**Nodes** are the entities in the graph. Their attributes include a unique `id` (usually the entity's main page name on Wikipedia), a `type` (one of six predefined types such as Person, Organization, Movement), a list of `aliases`, and `properties` (containing details like active period, birth/death dates, location, and a brief description).

**Relationships** define the connections between nodes. Their attributes include a `source` (source node id), a `target` (target node id), a `type` (one of twenty predefined types such as SUPERIOR\_OF/SUBORDINATE\_OF, MEMBER\_OF, PUSHED/BLOCKED), and `properties` (containing start/end times, positions, and supplementary descriptions).

This structured design is intended to provide a solid foundation for complex network analysis.

### Visualization

The project provides an interactive front-end visualization interface built with D3.js, located in the `docs/` directory. By opening the `docs/index.html` file in a browser, users can load and browse the graph data from `data/consolidated_graph.json`.

The interface supports filtering nodes and relationships by a time range, dynamically showing or hiding different types of nodes through a legend, and allowing users to highlight a node's direct network by clicking on it.

The size of a node correlates with its degree (number of connections) within the current time frame, offering an intuitive reference for its importance.

### How to Deploy

1.  Clone this repository to your local machine.
2.  Install all necessary Python dependencies: `pip install -r requirements.txt`.
3.  Create a `.env` file in the project root and set your `GOOGLE_API_KEY`.
4.  Fill in the entity names you wish to crawl in `data/LIST.txt`, categorized accordingly.
5.  Execute the data processing scripts in order: `python scripts/process_list.py`, then `python scripts/merge_graphs.py`.
6.  Open the `docs/index.html` file in a browser to view and interact with the data. To avoid local file access restrictions in browsers, it is recommended to access this file through a simple local HTTP server.

### Data Cleaning and Manual Review

To ensure the long-term health of the main graph, the `scripts/clean_data.py` script can be run periodically. It iterates through all nodes in the main graph, checks their corresponding Wikipedia link status, and separates "problem nodes"—those that are redirects, point to disambiguation pages, or have broken links—along with their associated relationships. These are saved into the `data_to_be_cleaned/` directory at the project root for manual review and correction. The script has a built-in caching mechanism to speed up repeated checks.

After manual review and correction, the data files can be moved to the `data/cleaned_data/` directory for archival, or their corrected content can be merged directly into the main graph file, `data/consolidated_graph.json`.

**Guidelines for Manual Review:**

1.  **Redirect Nodes**:

    Due to Simplified/Traditional character differences in the Chinese Wikipedia, some redirects where the `id` characters are correct but the link is to a Traditional Chinese index (e.g., "蒋中正" redirects to [https://zh.wikipedia.org/zh-cn/蔣中正](https://zh.wikipedia.org/zh-cn/蔣中正)) are automatically filtered by the script and not separated.

    Other redirects for the `id` fall into two categories:

    Firstly, cases where the phrasing does not match the official page name on Wikipedia (e.g., "中苏论战" [Sino-Soviet debate] has no separate page and redirects to [https://zh.wikipedia.org/zh-cn/九评苏共](https://zh.wikipedia.org/zh-cn/九评苏共) [Nine Commentaries on the Communist Party]).

    Secondly, cases where the entity is deprecated for historical reasons and has no separate page (e.g., "中国共产党中央军事部" [Central Military Department of the CCP] is part of the institutional evolution of the Central Military Commission and redirects to [https://zh.wikipedia.org/zh-cn/中央军事委员会](https://zh.wikipedia.org/zh-cn/中央军事委员会)).

    For the former, you can manually correct the `id` and move the common but unusable `id` name to the "aliases" array.

    For the latter, you can add the attribute `"verified_node": true` to bypass secondary checks.

    After making changes, it is recommended to also search for and adjust items in `relationships` that use this `id`, although this is optional (the front-end rendering will automatically remove redundant relationships with non-existent endpoints).

    Finally, merge the corrected data into the main graph file `data/consolidated_graph.json`.

    Example:

    ```sample1.json
    // Move "中苏论战" to the "aliases" array and change the id to "九评苏共"
    {
      "id": "九评苏共",
      "type": "Event",
      "aliases": [
        "中苏论战"
      ],
      "properties": {
        "period": "1960 - 1969",
        "location": "中国、苏联",
        "description": "The open ideological debate between the CCP and the CPSU, which was one of the background factors for the Cultural Revolution and led to the Sino-Soviet split."
      }
    }
    ```

    ```sample2.json
    // Add "verified_node"
    {
      "id": "中国共产党中央军事部",
      "type": "Organization",
      "aliases": [
        "中共中央军事部",
        "中央军事部"
      ],
      "properties": {
        "period": [
          "1925-12-12 - 1926-11",
          "1928-7 - 1930-2"
        ],
        "description": "On December 12, 1925, the CCP Central Committee announced the change of the Military Movement Committee to the Central Military Department. In early November 1926, it was changed to the Central Military Commission. In July 1928, the Central Military Department was formally re-established. In February 1930, the Military Department and the Military Commission were merged.",
        "verified_node": true
      }
    }
    ```

2.  **Disambiguation Nodes**:

    Please manually search Wikipedia to find the correct disambiguated item and change the `id`.

    For example, "中央人民政府" [Central People's Government] should have the correct `id` "中华人民共和国中央人民政府 (1949年—1954年)" [Central People's Government of the People's Republic of China (1949—1954)]. — Note the use of English parentheses, a single em dash, and including the character "年".

    Example:

    ```sample3.json
    // "中央人民政府" -> "中华人民共和国中央人民政府 (1949年—1954年)"
    {
      "id": "中华人民共和国中央人民政府 (1949年—1954年)",
      "type": "Organization",
      "aliases": [
        "中华人民共和国中央人民政府",
        "中国中央人民政府",
        "中华人民共和国中央人民政府 (1949年-1954年)",
        "中华人民共和国中央人民政府 (1949—1954)",
        "中华人民共和国中央人民政府 (1949-1954)",
        "中央人民政府",
        "中央人民政府委员会"
      ],
      "properties": {
        "period": "1949-10-01 - 1954-09-27",
        "location": "北京市",
        "description": "The central government of the People's Republic of China during its initial period from 1949 to 1954, serving as the highest organ of state power."
      }
    }
    ```

3.  **No-Link Nodes**:

    These nodes are generally divided into three categories:

    Firstly, cases where Wikipedia fails to redirect correctly. For example, "s:中华人民共和国宪法" [s:Constitution of the PRC] is an LLM output error.

    Secondly, Document-type nodes, such as "中华人民共和国国务院组织法" [Organic Law of the State Council of the PRC], which are archived on Wikisource (the Wikipedia page was deleted to avoid duplication).

    Thirdly, entities of low importance that do not have a Wikipedia page but are factually real. For example, "程宜芝" [Cheng Yizhi], Liu Bocheng's first wife, who does not have a separate Wikipedia page. Another example is "石家庄日报" [Shijiazhuang Daily], which also lacks a page.

    For the first category, the error can be corrected and then merged into the main graph file.

    For the second and third categories, you can add the attribute `"verified_node": true`.

### Project Status

This project is currently in the **Development** phase.

### Disclaimer

The data presented by this tool is for informational and research purposes only. All information is automatically generated from public sources and may be subject to the inherent delays and potential inaccuracies of those sources. When using the data, it should be independently verified with the provided official links or other primary sources.

### Contributing

This is an open-source project, and we highly welcome contributions of all forms. Whether you are a developer, data scientist, or domain expert, you can help by improving the extraction scripts, fine-tuning the LLM prompts, expanding the seed list, enhancing the front-end visualization, or reporting data inaccuracies. Please feel free to fork this repository, open an issue, or submit a pull request.