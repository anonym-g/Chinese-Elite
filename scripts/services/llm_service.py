# scripts/services/llm_service.py

import os
import json
import random
import sys
import copy
import logging
from google import genai
from google.genai import types

# 使用相对路径导入
from ..config import (
    PARSER_MODEL, 
    MERGE_CHECK_MODEL, MERGE_EXECUTE_MODEL, RELATION_CLEANER_MODEL, 
    VALIDATE_PR_MODEL,
    PARSER_SYSTEM_PROMPT_PATH, 
    MERGE_CHECK_PROMPT_PATH, MERGE_EXECUTE_PROMPT_PATH, CLEAN_SINGLE_RELATION_PROMPT_PATH,
    VALIDATE_PR_PROMPT_PATH, 
    MASTER_GRAPH_PATH,
    FEW_SHOT_NODE_SAMPLES, FEW_SHOT_REL_SAMPLES
)
from ..api_rate_limiter import (
    gemini_pro_limiter, 
    gemini_flash_limiter, gemini_flash_preview_limiter, gemini_flash_lite_limiter, 
    gemma_limiter
)
from . import graph_io

logger = logging.getLogger(__name__)

class LLMService:
    """
    一个统一的服务层，用于封装所有与大语言模型 (LLM) 的交互。
    
    该类负责:
    - 初始化 GenAI 客户端。
    - 加载和管理所有任务所需的 Prompt 模板。
    - 提供具体业务方法的接口 (如解析、合并、验证)，并在内部处理 API 调用、
      速率限制和错误处理。
    - 封装 few-shot 示例的生成逻辑。
    """
    def __init__(self):
        try:
            http_options = types.HttpOptions(timeout=360 * 1000)
            self.client = genai.Client(http_options=http_options)
            logger.info("Google GenAI Client 初始化成功 (超时设置为360秒)。")
        except Exception as e:
            logger.critical(f"严重错误: 初始化 Google GenAI Client 失败。请检查 API 密钥。", exc_info=True)
            sys.exit(1)
            
        # 一次性加载所有 Prompt 模板
        self.prompts = {
            'parser_system': self._load_prompt(PARSER_SYSTEM_PROMPT_PATH),
            'merge_check': self._load_prompt(MERGE_CHECK_PROMPT_PATH),
            'merge_execute': self._load_prompt(MERGE_EXECUTE_PROMPT_PATH),
            'clean_single_relation': self._load_prompt(CLEAN_SINGLE_RELATION_PROMPT_PATH),
            'validate_pr': self._load_prompt(VALIDATE_PR_PROMPT_PATH)
        }

    def _load_prompt(self, path: str) -> str:
        """加载指定路径的 Prompt 文件。"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            logger.critical(f"严重错误: Prompt 文件 '{path}' 未找到。")
            sys.exit(2)

    def _get_primary_name(self, node_id: str, node_obj: dict) -> str:
        """从节点对象中提取一个优先的、人类可读的名称。"""
        if not node_obj:
            return node_id
        name_obj = node_obj.get('name', {})
        # 优先级: zh-cn -> en -> 其他语言的第一个 -> 节点ID
        return (
            name_obj.get('zh-cn', [None])[0] or
            name_obj.get('en', [None])[0] or
            next((names[0] for lang, names in name_obj.items() if names), node_id)
        )

    def _get_few_shot_examples(self) -> str:
        """从主图谱文件中随机抽取节点和关系作为few-shot范例。"""
        if not os.path.exists(MASTER_GRAPH_PATH):
            return ""
        try:
            data = graph_io.load_master_graph(MASTER_GRAPH_PATH)
            nodes = data.get('nodes', [])
            relationships = data.get('relationships', [])
            if not nodes or not relationships: return ""

            id_to_name_map = {}
            for n in nodes:
                node_id = n.get('id')
                if not node_id: continue
                
                primary_name = self._get_primary_name(node_id, n)
                id_to_name_map[node_id] = primary_name
            
            node_samples = random.sample(nodes, min(len(nodes), FEW_SHOT_NODE_SAMPLES))
            rel_samples = random.sample(relationships, min(len(relationships), FEW_SHOT_REL_SAMPLES))

            readable_node_samples = []
            for node in node_samples:
                node_copy = copy.deepcopy(node)
                node_copy['id'] = id_to_name_map.get(node['id'], node['id'])
                if 'properties' in node_copy and 'verified_node' in node_copy['properties']:
                    del node_copy['properties']['verified_node']
                readable_node_samples.append(node_copy)

            readable_rel_samples = []
            for rel in rel_samples:
                rel_copy = copy.deepcopy(rel)
                rel_copy['source'] = id_to_name_map.get(rel['source'], rel['source'])
                rel_copy['target'] = id_to_name_map.get(rel['target'], rel['target'])
                readable_rel_samples.append(rel_copy)

            if not readable_node_samples and not readable_rel_samples: return ""
            
            examples = {"nodes": readable_node_samples, "relationships": readable_rel_samples}
            return f"\n请参考以下JSON格式样例来构建你的输出。\n--- JSON格式样例 START ---\n{json.dumps(examples, indent=2, ensure_ascii=False)}\n--- JSON格式样例 END ---\n"
        except Exception as e:
            logger.warning(f"读取或生成 few-shot 范例失败 - {e}")
            return ""

    @gemini_pro_limiter.limit
    def parse_wikitext(self, wikitext: str) -> dict | None:
        """使用 LLM 从 Wikitext 解析实体和关系。"""
        few_shot_examples = self._get_few_shot_examples()
        user_prompt = f"{few_shot_examples}\n请严格遵循你的核心指令，根据你的知识和以下Wikitext内容，进行实体和关系提取。\n--- WIKITEXT START ---\n{wikitext}\n--- WIKITEXT END ---"
        logger.info(f"正在通过 LLM ({PARSER_MODEL}) 进行解析...")
        if few_shot_examples:
            logger.info(f"已注入 {FEW_SHOT_NODE_SAMPLES} 个节点和 {FEW_SHOT_REL_SAMPLES} 个关系作为 few-shot 范例。")

        try:
            response = self.client.models.generate_content(
                model=f'models/{PARSER_MODEL}', contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=self.prompts['parser_system'],
                    response_mime_type='application/json',
                ),
            )
            if response.text:
                logger.info("LLM 解析成功。")
                return json.loads(response.text)
            return None
        except Exception as e:
            logger.error(f"LLM API 调用 (解析Wikitext) 失败 - {e}")
            return None

    @gemma_limiter.limit
    def should_merge(self, existing_item: dict, new_item: dict) -> bool:
        """调用LLM判断新对象是否提供了有价值的新信息。"""
        keys_to_remove = {'id', 'name', 'source', 'target'}
        existing_props = {k: v for k, v in existing_item.items() if k not in keys_to_remove}
        new_props = {k: v for k, v in new_item.items() if k not in keys_to_remove}

        prompt = (f"{self.prompts['merge_check']}\n"
                  f"--- 现有JSON对象 ---\n{json.dumps(existing_props, indent=2, ensure_ascii=False)}\n"
                  f"--- 新JSON对象 ---\n{json.dumps(new_props, indent=2, ensure_ascii=False)}\n"
                  f"--- 新对象是否提供了有价值的新信息？ (回答 YES 或 NO) ---")
        try:
            response = self.client.models.generate_content(model=f'models/{MERGE_CHECK_MODEL}', contents=prompt)
            return response.text.strip().upper() == "YES" if response.text else True
        except Exception:
            return True # 默认返回True以进行合并，确保数据不会丢失

    @gemini_flash_limiter.limit
    def merge_items(self, existing_item: dict, new_item: dict, item_type: str) -> dict:
        """调用LLM执行两个冲突项的智能合并。"""
        keys_to_remove = {'id', 'name', 'source', 'target'}
        existing_props = {k: v for k, v in existing_item.items() if k not in keys_to_remove}
        new_props = {k: v for k, v in new_item.items() if k not in keys_to_remove}
        prompt = (f"--- 现有{item_type} ---\n{json.dumps(existing_props, indent=2, ensure_ascii=False)}\n"
                  f"--- 新{item_type} ---\n{json.dumps(new_props, indent=2, ensure_ascii=False)}\n"
                  f"--- 合并后的最终JSON ---\n")
        try:
            response = self.client.models.generate_content(
                model=f'models/{MERGE_EXECUTE_MODEL}', contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=self.prompts['merge_execute'],
                    response_mime_type='application/json',
                ),
            )
            if response.text:
                merged_props = json.loads(response.text)
                final_item = existing_item.copy()
                final_item.update(merged_props)
                return final_item
        except Exception as e:
            logger.error(f"LLM 合并失败 - {e}")
        return existing_item # 合并失败时返回原始项

    @gemini_flash_lite_limiter.limit
    def is_relation_deletable(self, relation: dict, id_to_node_map: dict) -> bool | None:
        """
        使用LLM判断单条关系是否应被删除。
        返回 True 表示应删除, False 表示应保留。 None 表示API调用失败。
        """
        rel_copy = copy.deepcopy(relation)

        def _format_node_info(node_id: str) -> str:
            """内部辅助函数，用于格式化节点信息字符串。"""
            node = id_to_node_map.get(node_id)
            if not node:
                return node_id or "Unknown"

            primary_name = self._get_primary_name(node_id, node)
            
            node_type = node.get('type', 'Unknown')
            return f"{primary_name} (Type: {node_type})"

        # 1. 调用 _format_node_info 之前，先获取并验证 source_id 和 target_id
        source_id = relation.get('source')
        target_id = relation.get('target')

        # 2. 如果 source_id 或 target_id 不是有效的字符串，说明关系数据本身有问题，标记为可删除
        if not isinstance(source_id, str) or not isinstance(target_id, str):
            logger.warning(f"关系格式错误，缺少 source 或 target ID，将标记为可删除: {relation}")
            return True

        # 3. 验证通过后，调用 _format_node_info
        rel_copy['source'] = _format_node_info(source_id)
        rel_copy['target'] = _format_node_info(target_id)

        prompt = self.prompts['clean_single_relation'] + "\n" + json.dumps(rel_copy, indent=2, ensure_ascii=False)

        # --- 打印发送给LLM的完整内容 ---
        logger.info(f"向LLM发送关系审查请求:\n{json.dumps(rel_copy, indent=2, ensure_ascii=False)}")

        try:
            response = self.client.models.generate_content(
                model=f'models/{RELATION_CLEANER_MODEL}', contents=prompt
            )

            # --- 打印LLM的原始返回 ---
            raw_response_text = response.text if hasattr(response, 'text') else "N/A"
            logger.info(f"LLM原始返回: '{raw_response_text}'")

            if response.text:
                decision = response.text.strip().upper()
                
                if 'FALSE' in decision:
                    logger.info("LLM决策: False (保留)")
                    return False
                if 'TRUE' in decision:
                    logger.info("LLM决策: True (删除)")
                    return True
            return None # 如果响应不是明确的TRUE/FALSE，则视为失败
        except Exception as e:
            logger.warning(f"LLM 关系清洗API调用失败: {e}")
            return None

    @gemini_flash_preview_limiter.limit
    def validate_pr_diff(self, diff_content: str, file_name: str) -> str | None:
        """调用LLM评估PR的diff内容。"""
        prompt = self.prompts['validate_pr'].format(
            file_name=file_name,
            diff_content=diff_content[:15000] # 限制内容长度
        )
        try:
            response = self.client.models.generate_content(
                model=f'models/{VALIDATE_PR_MODEL}', contents=prompt
            )
            decision = response.text.strip() if response.text else None
            return decision if decision in ["True", "False"] else None
        except Exception as e:
            logger.error(f"LLM API 调用 (PR验证) 失败: {e}")
            return None
