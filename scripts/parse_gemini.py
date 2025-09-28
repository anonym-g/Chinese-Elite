# scripts/parse_gemini.py

import os
import json
import random
import sys
from dotenv import load_dotenv
from google import genai
from google.genai import types

from config import (
    PARSER_MODEL, PARSER_SYSTEM_PROMPT_PATH, CONSOLIDATED_GRAPH_PATH,
    FEW_SHOT_NODE_SAMPLES, FEW_SHOT_REL_SAMPLES
)

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
            print(f"[!] 严重错误: 系统Prompt文件未找到于 '{system_prompt_path}'", file=sys.stderr)
            sys.exit(1)

    def _get_few_shot_examples(self) -> str:
        """从主图谱文件中随机抽取节点和关系作为few-shot范例。"""
        if not os.path.exists(CONSOLIDATED_GRAPH_PATH):
            return ""
        try:
            with open(CONSOLIDATED_GRAPH_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            nodes = data.get('nodes', [])
            relationships = data.get('relationships', [])

            node_samples = random.sample(nodes, min(len(nodes), FEW_SHOT_NODE_SAMPLES))
            rel_samples = random.sample(relationships, min(len(relationships), FEW_SHOT_REL_SAMPLES))

            for node in node_samples:
                if 'properties' in node and 'verified_node' in node['properties']:
                    del node['properties']['verified_node']

            if not node_samples and not rel_samples:
                return ""
            
            examples = {"nodes": node_samples, "relationships": rel_samples}
            example_str = json.dumps(examples, indent=2, ensure_ascii=False)
            
            return f"""
请参考以下高质量的JSON格式样例来构建你的输出。这只是格式参考，你不需要提取与样例完全一样的内容。

--- JSON格式样例 START ---
{example_str}
--- JSON格式样例 END ---
"""
        except (IOError, json.JSONDecodeError) as e:
            print(f"[!] 警告：读取或解析 few-shot 范例文件失败 - {e}", file=sys.stderr)
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
        print(f"[*] 正在通过 Google GenAI SDK ({self.model_name}) 进行解析...")
        if few_shot_examples:
            print(f"[*] 已注入 {FEW_SHOT_NODE_SAMPLES} 个节点和 {FEW_SHOT_REL_SAMPLES} 个关系作为 few-shot 范例。")

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
                print("[*] LLM解析成功，已获取结构化数据。")
                return parsed_json
            else:
                print("[!] 错误：LLM返回的内容为空。", file=sys.stderr)
                return None
        except Exception as e:
            print(f"[!] 错误：LLM API调用失败 - {e}", file=sys.stderr)
            return None
