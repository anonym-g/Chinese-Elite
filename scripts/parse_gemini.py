# scripts/parse_gemini.py

import os
import json
import random
import sys
from dotenv import load_dotenv
from google import genai
from google.genai import types
import copy
import logging

from config import (
    PARSER_MODEL, PARSER_SYSTEM_PROMPT_PATH, MASTER_GRAPH_PATH,
    FEW_SHOT_NODE_SAMPLES, FEW_SHOT_REL_SAMPLES
)

logger = logging.getLogger(__name__)

# 加载 .env 文件中的环境变量
load_dotenv()

class GeminiParser:
    """使用Google Gemini API解析Wikitext的封装类。"""

    def __init__(self, model_name=PARSER_MODEL, system_prompt_path=PARSER_SYSTEM_PROMPT_PATH):
        self.client = genai.Client()
        self.model_name = model_name
        try:
            with open(system_prompt_path, 'r', encoding='utf-8') as f:
                self.system_prompt = f.read()
        except FileNotFoundError:
            logger.critical(f"严重错误: '{system_prompt_path}'未找到系统Prompt文件")
            sys.exit(1)

    def _get_few_shot_examples(self) -> str:
        """从主图谱文件中随机抽取节点和关系作为few-shot范例，并将ID转换为可读名称。"""
        if not os.path.exists(MASTER_GRAPH_PATH):
            return ""
        try:
            with open(MASTER_GRAPH_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            nodes = data.get('nodes', [])
            relationships = data.get('relationships', [])

            if not nodes or not relationships:
                return ""

            # 步骤 1: 创建一个从所有节点ID到其主要中文名的映射
            id_to_name_map = {
                node['id']: node.get('name', {}).get('zh-cn', [node['id']])[0]
                for node in nodes
            }

            # 步骤 2: 随机抽样
            node_samples = random.sample(nodes, min(len(nodes), FEW_SHOT_NODE_SAMPLES))
            rel_samples = random.sample(relationships, min(len(relationships), FEW_SHOT_REL_SAMPLES))

            # 步骤 3: 将抽样数据的ID转换为可读名称
            readable_node_samples = []
            for node in node_samples:
                node_copy = copy.deepcopy(node)
                # 将节点的 'id' 替换为它的可读名称
                node_copy['id'] = id_to_name_map.get(node['id'], node['id'])
                # 清理不必要的属性
                if 'properties' in node_copy and 'verified_node' in node_copy['properties']:
                    del node_copy['properties']['verified_node']
                readable_node_samples.append(node_copy)

            readable_rel_samples = []
            for rel in rel_samples:
                rel_copy = copy.deepcopy(rel)
                # 将关系的 'source' 和 'target' ID 替换为可读名称
                rel_copy['source'] = id_to_name_map.get(rel['source'], rel['source'])
                rel_copy['target'] = id_to_name_map.get(rel['target'], rel['target'])
                readable_rel_samples.append(rel_copy)

            if not readable_node_samples and not readable_rel_samples:
                return ""
            
            examples = {"nodes": readable_node_samples, "relationships": readable_rel_samples}
            example_str = json.dumps(examples, indent=2, ensure_ascii=False)
            
            return f"""
请参考以下高质量的JSON格式样例来构建你的输出。这只是格式参考，你不需要提取与样例完全一样的内容。

--- JSON格式样例 START ---
{example_str}
--- JSON格式样例 END ---
"""
        except (IOError, json.JSONDecodeError) as e:
            logger.warning(f"读取或解析 few-shot 范例文件失败 - {e}")
            return ""

    def parse(self, wikitext: str) -> dict | None:
        """
        主解析方法，构建完整Prompt并调用Gemini API。
        """
        few_shot_examples = self._get_few_shot_examples()
        user_prompt = f"""
{few_shot_examples}

请严格遵循你的核心指令，根据你的知识和以下Wikitext内容，进行实体和关系提取。

--- WIKITEXT START ---
{wikitext}
--- WIKITEXT END ---
"""
        logger.info(f"正在通过 Google GenAI SDK ({self.model_name}) 进行解析...")
        if few_shot_examples:
            logger.info(f"已注入 {FEW_SHOT_NODE_SAMPLES} 个节点和 {FEW_SHOT_REL_SAMPLES} 个关系作为 few-shot 范例。")

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=self.system_prompt,
                    response_mime_type='application/json',
                ),
            )
            response_content = response.text
            
            if response_content:
                parsed_json = json.loads(response_content)
                logger.info("LLM解析成功，已获取结构化数据。")
                return parsed_json
            else:
                logger.error("LLM返回的内容为空。")
                return None
        except Exception as e:
            logger.error(f"LLM API调用失败 - {e}")
            return None
