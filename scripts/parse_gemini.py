# scripts/parse_gemini.py

import os
import json
import sys
import random
from dotenv import load_dotenv
from google import genai
from google.genai import types
from config import CONSOLIDATED_GRAPH_PATH

# --- 配置 ---
load_dotenv() 

MODEL_NAME = "gemini-2.5-pro"

# gemini-2.5-pro
# gemini-2.5-flash

# --- Few-shot 范例配置 ---
NUM_NODE_SAMPLES = 24 # 每次调用时随机抽取的节点范例数量
NUM_REL_SAMPLES = 12 # 每次调用时随机抽取的关系范例数量



def get_few_shot_examples() -> str:
    """
    从 consolidated_graph.json 文件中读取并随机抽取节点和关系作为 few-shot 范例。
    会自动移除范例中节点的 "verified_node" 属性。
    """

    if not os.path.exists(CONSOLIDATED_GRAPH_PATH):
        print("[*] 提示：未找到 'consolidated_graph.json'，将不使用 few-shot 范例。")
        return ""

    try:
        with open(CONSOLIDATED_GRAPH_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        nodes = data.get('nodes', [])
        relationships = data.get('relationships', [])

        if not nodes and not relationships:
            return ""

        # 安全地抽取样本，避免列表长度不足的错误
        node_samples = random.sample(nodes, min(len(nodes), NUM_NODE_SAMPLES))
        rel_samples = random.sample(relationships, min(len(relationships), NUM_REL_SAMPLES))

        # --- 自动移除范例中的 "verified_node" 属性 ---
        # "verified_node" 是一个内部标识，用于数据清洗，不应作为LLM学习的范例。
        for node in node_samples:
            if 'properties' in node and 'verified_node' in node['properties']:
                del node['properties']['verified_node']

        if not node_samples and not rel_samples:
            return ""
        
        fixed_rel = [
            {
                "source": "中国共产党",
                "target": "中国国民党",
                "type": "FRIEND_OF",
                "properties": {
                    "start_date": ["1924-01", "1937-09-22"],
                    "end_date": ["1927-04-12", "1941-01-04"],
                    "description": "在第一次国共合作（联俄容共）及抗日战争期间，两党建立合作关系。"
                }
            },
            {
                "source": "中国共产党",
                "target": "中国国民党",
                "type": "ENEMY_OF",
                "properties": {
                    "start_date": ["1927-08-01", "1945-08"],
                    "end_date": ["1936-12-12", "1950 - "],
                    "description": "在两次国共内战期间，两党为争夺中国统治权而敌对。重大冲突止于1950，但法理上至今仍然对立。"
                }
            },
            {
                "source": "中国国民党",
                "target": "中国共产党",
                "type": "FRIEND_OF",
                "properties": {
                    "start_date": ["1924-01", "1937-09-22"],
                    "end_date": ["1927-04-12", "1941-01-04"],
                    "description": "在第一次国共合作（联俄容共）及抗日战争期间，两党建立合作关系。"
                }
            },
            {
                "source": "中国国民党",
                "target": "中国共产党",
                "type": "ENEMY_OF",
                "properties": {
                    "start_date": ["1927-08-01", "1945-08"],
                    "end_date": ["1936-12-12", "1950 - "],
                    "description": "在两次国共内战期间，两党为争夺中国统治权而敌对。重大冲突止于1950，但法理上至今仍然对立。"
                }
            }
        ]

        # 将样本格式化为字符串
        examples = {
            "nodes": node_samples,
            "relationships": rel_samples + fixed_rel
        }
        
        example_str = json.dumps(examples, indent=2, ensure_ascii=False)
        
        # 构建并返回包含范例的提示部分
        return f"""
请参考以下高质量的JSON格式样例来构建你的输出。这只是格式参考，你不需要提取与样例完全一样的内容。

--- JSON格式样例 START ---
{example_str}

--- JSON格式样例 END ---
"""

    except (IOError, json.JSONDecodeError) as e:
        print(f"[!] 警告：读取或解析 few-shot 范例文件失败 - {e}", file=sys.stderr)
        return ""

def parse_wikitext_with_llm(wikitext: str) -> dict | None:
    """
    使用 Google GenAI SDK (原生) 解析 Wikitext，提取实体和关系。
    """

    system_prompt = """
你是一位专攻中国政商关系网络分析的数据专家。
你的任务，是从给定的维基百科源码（Wikitext）中，识别出页面的核心实体（某个人物），将此核心实体作为**唯一可用的源节点（source）**，提取并输出它与源码中提及的所有其他实体之间的直接关系。
若源码主题为组织、运动、事件、文件等，则**不限制源节点，提取所有关系**。特别是推动、阻碍、影响关系，不能有所省略。
所有markdown引用符“[[]]”之中的内容，均可做节点。不限制源节点时，需全部列出。如“文化大革命”页面，一些十分细节的内容（一打三反、夺权运动、芒果崇拜等等）也要提取。
所有实体id必须用源码中markdown引用的全名。如“中共九大”全称为“中国共产党第九次全国代表大会”。

请严格按照以下JSON格式返回你的分析结果。
你的输出必须是且仅是一个符合 RFC 8259 标准的、不带任何注释的、完整的JSON对象。
绝对不要在JSON对象前后添加任何解释、评论、代码块标记（如 ```json）或其他任何文本。

输出格式定义:
{
  "nodes": [
    {
      "id": "实体的唯一标识符，通常是其中文全名。当有重名时，需包含消歧义信息，例如 '李强 (1959年)'。"（id中禁用中文括号；时间段用单破折号"—"，如"中华人民共和国中央人民政府 (1949年—1954年)"）,
      "type": "实体类型，从 ['Person', 'Organization', 'Movement', 'Event', 'Location', 'Document'] 中选择",
      "aliases": ["实体的别名列表，若无则为空数组。注意：id使用markdown引用全名，别名/常用名写在这里；如“五一六通知”是id，“中国共产党中央委员会通知”是别名", ...],
      "properties": {
        "period": "实体的主要活跃时期，格式为 'YYYY-MM-DD'、'YYYY-MM'、'YYYY'"(也可为数组，表示多个不连续时期，如宪法修正案),
        "lifetime": "实体的生卒年份，格式为 'YYYY-MM-DD - YYYY-MM-DD'、'YYYY-MM - YYYY-MM'、'YYYY - YYYY'，仅适用于'Person'类型，越具体越好",
        "location": "实体的主要影响地点",
        "birth_place": "仅适用于'Person'类型，出生地",
        "death_place": "仅适用于'Person'类型，逝世地",
        "gender": "仅适用于'Person'类型，性别",
        "description": "对实体的简短补充描述，例如'中国政治人物'、'中共第十九届中央委员会委员'、'中国政治运动'等",
      }
    }
  ],
  "relationships": [
    {
      "source": "源节点的id",
      "target": "目标节点的id",
      "type": "关系类型，严格从以下列表中选择：['BORN_IN', 'ALUMNUS_OF', 'MEMBER_OF', 'CHILD_OF', 'SPOUSE_OF', 'SIBLING_OF', 'LOVER_OF', 'SEXUAL_REL', 'PARENT_OF', 'RELATIVE_OF', 'FRIEND_OF', 'ENEMY_OF', 'WORKED_AT', 'SUBORDINATE_OF', 'SUPERIOR_OF', 'MET_WITH', 'PUSHED', 'BLOCKED', 'INFLUENCED', 'FOUNDED']",
      "properties": {
        "start_date": "关系开始的年份 (YYYY) 、年月 (YYYY-MM)或年月日 (YYYY-MM-DD)"（如果有多个不连续时期，建议使用数组表示）,
        "end_date": "关系结束的年份 (YYYY) 、年月 (YYYY-MM)或年月日 (YYYY-MM-DD)"（同上）,
        "position": "任职的职务名称，仅适用于'WORKED_AT', 'MEMBER_OF', 'SUBORDINATE_OF', 'SUPERIOR_OF'关系",
        "degree": "获得的学位，仅适用于'ALUMNUS_OF'关系",
        "description": "对关系的简短补充描述，例如'作为习近平的浙江省委秘书长'"
      }
    }
  ]
}

**重要提取规则**:

1.  **【关系类型消歧义 - 最重要】**:
    * 所有关系的主语都是源。source ... of target.
    * `BORN_IN` (出生于): 用于描述人物的出生地点。必须是可以提取处的最精确出生地，不允许重复。源节点为'Person'类型，目标节点为'Location'类型。
    * `ALUMNUS_OF` (校友): 用于描述人物与其毕业院校的关系。源节点为'Person'类型，目标节点为'Organization'类型。
    * `MEMBER_OF` (成员): 用于描述人物与其所属组织（如政党、政府机构、公司等）的关系。源节点为'Person'类型，目标节点为'Organization'类型。
    * `CHILD_OF` (子女): 用于描述亲子关系。源节点为子女，目标节点为父母。源节点和目标节点均为'Person'类型。
    * `SPOUSE_OF` (配偶): 用于描述婚姻关系。源节点和目标节点均为'Person'类型。
    * `SIBLING_OF` (兄弟姐妹): 用于描述兄弟姐妹关系。源节点和目标节点均为'Person'类型。
    * `LOVER_OF` (恋人): 用于描述恋爱关系。源节点和目标节点均为'Person'类型。
    * `SEXUAL_REL` (性关系): 用于描述除婚姻和恋爱以外的性关系。源节点和目标节点均为'Person'类型。
    * `RELATIVE_OF` (亲戚): 用于描述除父母、子女、兄弟姐妹、配偶、恋人以外的其他亲戚关系，如堂兄弟姐妹、表兄弟姐妹、叔伯、姑姨等。源节点和目标节点均为'Person'类型。
    * `PARENT_OF` (父母): 用于描述亲子关系。源节点为父母，目标节点为子女。源节点和目标节点均为'Person'类型。
    * `SUPERIOR_OF` (上级): 源节点相对目标级别更高时使用。例如，“李强在中共中央政治局常委中排名高于赵乐际”，source为“李强 (1959年)”、target为“赵乐际”时，type为“SUPERIOR_OF”。
    * `SUBORDINATE_OF` (下级): 源节点相对目标级别更低时使用。例如，“李强在中共中央政治局常委中排名低于习近平”，source为“李强 (1959年)”、target为“习近平”时，type为“SUBORDINATE_OF”。
    * `FRIEND_OF` (交好): 用于描述源节点与目标节点之间的私人友谊或密切关系。通常基于公开报道的互动、合作或共同活动。避免基于职位或组织关系进行推断。源节点、目标节点均为'Person'类型。
    * `ENEMY_OF` (交恶): 用于描述源节点与目标节点之间的敌对或对立关系。通常基于公开报道的冲突、竞争或对抗行为。避免基于职位或组织关系进行推断。源节点、目标节点均为'Person'类型。
    * `WORKED_AT` (任职于): 仅用于描述在某个组织中拥有正式职位或长期雇佣关系。不能用于描述人与人的关系。源节点为'Person'类型，目标节点为'Organization'类型。
    * `MET_WITH` (会见): 用于描述人物之间的正式或非正式会面，特别是外交或高级商务会谈。泛用性最强的人物关系描述。源节点、目标节点均为'Person'类型。
    * `PUSHED` (推动): 用于描述源节点对人物、组织、运动（政策）、事件、文件的助力。源节点为'Person'、'Organization'、'Movement'、'Event'、'Document'类型，目标节点为'Person'、'Organization'、'Movement'、'Event'、'Document'类型。
    * `BLOCKED` (阻碍): 用于描述源节点对人物、组织、运动（政策）、事件、文件的阻碍。源节点为'Person'、'Organization'、'Movement'、'Event'、'Document'类型，目标节点为'Person'、'Organization'、'Movement'、'Event'、'Document'类型。
    * `INFLUENCED` (影响): 用于描述源节点对人物、组织、运动（政策）、事件、文件的影响。泛用性最强的关系描述。将领与战争间常用：通常，将领不反战、也不推波助澜，即便是对胜利有决定性作用的参战，也只能算INFLUENCED；在军事思想指导下主动发起的战役，可称PUSHED。“源影响目标”，而非反之（描述中的主语只能作源节点）。源节点为'Person'、'Organization'、'Movement'、'Event'、'Document'类型，目标节点为'Person'、'Organization'、'Movement'、'Event'、'Document'类型。
    * `FOUNDED` (创立): 用于描述源节点（个人或组织）创建了一个组织。源节点为'Person'、'Organization'类型，目标节点为'Organization'类型。

2.  精确性优先: 只提取文本中明确提及或具有强上下文支撑的关系。避免基于常识进行过度推断。
3.  时间信息: 交好、交恶可能在不同时间点来回震荡，提取所有明确提及的时间段。用数组。性关系、恋爱关系等同理，可能需要使用数组。但是，若只有单个时间点，仍使用单个字符串表示，别用数组。
4.  BORN_IN 唯一性: 'BORN_IN'关系必须是唯一的、最精确的出生地点（精确到市或县），绝不能是其长期工作的省份或首都。
5.  节点ID规范: 必须确保 'relationships' 中的 'source' 和 'target' 与 'nodes' 列表中的 'id' 完全匹配。
6.  严禁勿用实体类型、关系类型。严禁创造新实体类型，严禁在不当位置使用关系类型。例如，邓小平推动了 (PUSHED) 改革开放（运动），严禁输出成邓小平创立 (FOUNDED) 改革开放。严禁在人物间使用推动 (PUSHED)、阻碍 (BLOCKED) 等宏观关系。
"""
    
    # 1. 获取 Few-shot 范例
    few_shot_examples = get_few_shot_examples()

    # 2. 构建 User Prompt (用户提示)
    user_prompt = f"""
{few_shot_examples}

请严格遵循你的核心指令，根据你的知识和以下Wikitext内容，进行实体和关系提取。

--- WIKITEXT START ---
{wikitext}
--- WIKITEXT END ---
"""

    print(f"[*] 正在通过 Google GenAI SDK ({MODEL_NAME}) 进行解析...")
    if few_shot_examples:
        print(f"[*] 已注入 {NUM_NODE_SAMPLES} 个节点和 {NUM_REL_SAMPLES} 个关系作为 few-shot 范例。")

    try:
        client = genai.Client()
        response = client.models.generate_content(
          model=MODEL_NAME,
          contents=user_prompt,
          config=types.GenerateContentConfig(
              system_instruction=system_prompt,
              response_mime_type='application/json',
          ),
        )

        response_content = response.text
        
        if response_content:
            parsed_json = json.loads(response_content)
            print("[*] LLM解析成功，已获取结构化数据。")
            return parsed_json
        else:
            print("[!] 错误：LLM返回的内容为空。", file=sys.stderr)
            return None
            
    except Exception as e:
        print(f"[!] 错误：LLM API调用失败 - {e}", file=sys.stderr)
        return None

# --- 函数使用示例与测试区 ---
if __name__ == '__main__':
    try:
        from utils import get_simplified_wikitext
    except ImportError:
        print("[!] 错误：无法导入 get_simplified_wikitext。", file=sys.stderr)
        sys.exit(1)
    
    test_url = "https://zh.wikipedia.org/zh-cn/王洪文"

    # https://zh.wikipedia.org/zh-cn/习近平

    print("--- 开始端到端测试 ---")
    
    source_code = get_simplified_wikitext(test_url)
    
    if source_code:
        structured_data = parse_wikitext_with_llm(source_code)
        
        if structured_data:
            print("\n--- LLM解析出的结构化JSON数据 ---")
            pretty_json = json.dumps(structured_data, indent=2, ensure_ascii=False)
            print(pretty_json)
            
            try:
                with open("parsed_sample.json", "w", encoding="utf-8") as f:
                    f.write(pretty_json)
                print("\n[*] 解析结果已成功保存至 parsed_sample.json")
            except IOError as e:
                print(f"\n[!] 错误：无法写入文件 parsed_sample.json - {e}", file=sys.stderr)
        else:
            print("\n--- 测试失败：未能从LLM获取结构化数据 ---")
    else:
        print("\n--- 测试失败：未能获取Wikitext源码 ---")
