import json
from pathlib import Path

# 假设语言文件放在同目录下
LANG_FILE = Path("lang.json")
current_lang = "zh" # 默认语言

def load_lang(lang_code="zh"):
    global current_lang
    current_lang = lang_code

def t(key, **kwargs):
    """获取翻译字符串并支持变量替换"""
    try:
        with open(LANG_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # 获取对应语言的字典
            lang_data = data.get(current_lang, data.get("zh", {}))
            text = lang_data.get(key, f"[{key}]") # 若找不到键，返回 [key]
            return text.format(**kwargs)
    except Exception:
        return f"[{key}]"