# scripts/utils.py

import requests
from urllib.parse import urlparse, urlunparse
from opencc import OpenCC
import sys

def get_simplified_wikitext(url: str) -> str | None:
    """
    将多种格式的中文维基百科URL转换为标准源码URL，获取其Wikitext，
    并将其从台湾正体（繁体）转换为简体中文。

    Args:
        url: 中文维基百科的文章链接，例如包含 /wiki/, /zh/, /zh-cn/ 等路径。

    Returns:
        转换后的简体中文Wikitext字符串，若过程中发生错误则返回None。
    """
    
    try:
        # 1. 解析输入URL并提取文章标题
        parsed_url = urlparse(url)
        path_segments = parsed_url.path.strip('/').split('/')
        
        # 文章标题通常是路径的最后一部分
        if not path_segments:
            raise ValueError("URL路径为空或格式不正确")
        
        article_title = path_segments[-1]
        
        # 2. 构建稳定、统一的原始Wikitext获取URL (action=raw)
        # action=raw 比 action=edit 更适合程序化获取纯文本源码
        raw_url_parts = (
            'https',
            'zh.wikipedia.org',
            '/w/index.php',
            '',
            f'title={article_title}&action=raw',
            ''
        )
        raw_url = urlunparse(raw_url_parts)
        
        print(f"[*] 已将URL转换为: {raw_url}")

    except (ValueError, IndexError) as e:
        print(f"[!] 错误：无法从输入URL '{url}' 解析文章标题 - {e}", file=sys.stderr)
        return None

    # 3. 发起网络请求获取原始Wikitext
    headers = {
        'User-Agent': 'ChineseEliteExplorer/1.0 (https://github.com/anonym-g/Chinese-Elite)'
    }
    
    print(f"[*] 正在获取 '{article_title}' 的Wikitext源码...")
    try:
        response = requests.get(raw_url, headers=headers, timeout=15)
        response.raise_for_status()  # 如果请求失败 (如 404), 则抛出异常
        
        # 维基百科源码通常使用UTF-8编码
        traditional_wikitext = response.text
        
    except requests.exceptions.RequestException as e:
        print(f"[!] 错误：获取Wikitext失败 - {e}", file=sys.stderr)
        return None

    # 4. 执行简繁转换（台湾正体 -> 简体）
    print("[*] 正在将Wikitext转换为简体中文...")
    try:
        # 't2s.json' 是OpenCC中从繁体(Traditional)到简体(Simplified)的标准配置
        # 它默认以台湾地区用词习惯为准，符合要求
        cc = OpenCC('t2s')
        simplified_wikitext = cc.convert(traditional_wikitext)
        print("[*] 转换完成。")
        
        return simplified_wikitext
        
    except Exception as e:
        print(f"[!] 错误：OpenCC转换失败 - {e}", file=sys.stderr)
        return None

# --- 函数使用示例与测试区 ---
if __name__ == '__main__':
    test_urls = [
        "https://zh.wikipedia.org/zh-cn/%E4%B8%AD%E5%9B%BD%E5%85%B1%E4%BA%A7%E5%85%9A",
        "https://zh.wikipedia.org/wiki/%E6%9D%8E%E5%BC%BA_(1959%E5%B9%B4)",
        "https://zh.wikipedia.org/wiki/%E6%98%93%E4%BC%9A%E6%BB%A1"
    ]

    for i, test_url in enumerate(test_urls):
        print(f"\n--- 测试URL {i+1} ---")
        wikitext = get_simplified_wikitext(test_url)
        
        if wikitext:
            print("\n--- 获取到的简体Wikitext源码 (前500字符) ---")
            print(wikitext[:500] + "...")
            
            # 将完整源码写入文件以便检查
            file_name = f"wikitext_sample_{i+1}.md"
            with open(file_name, "w", encoding="utf-8") as f:
                f.write(wikitext)
            print(f"\n[*] 完整源码已保存至 {file_name}")
