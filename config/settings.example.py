"""
ArXiv API 配置文件
"""
import os

# arXiv API 搜索配置
SEARCH_CONFIG = {
    'max_total_results': 200,         # 总共要获取的最大论文数量
    'page_size': 100,                 # 每页请求数量，避免单次请求过大
    'delay_seconds': 10,              # arXiv API 分页/内部重试的最小间隔
    'num_retries': 5,                 # arXiv 客户端对 429/5xx 的内部重试次数
    'workflow_retry_attempts': 3,     # 完整检索流程的最大尝试次数
    'workflow_retry_delay': 60,       # 完整检索重试的初始等待秒数（指数退避）
    'allow_stale_on_transient_error': True,  # 有历史摘要时允许上游限流降级为空更新
    'sort_by': 'SubmittedDate',       # 排序方式: Relevance, LastUpdatedDate, SubmittedDate
    'sort_order': 'Descending',       # 排序顺序: Ascending, Descending
    'include_cross_listed': True,     # 是否包含跨类别的论文
    'abstracts': True,                # 是否包含摘要
    'id_list': None,                  # 按ID搜索特定论文
    'title_only': False,              # 是否仅在标题中搜索
    'author_only': False,             # 是否仅搜索作者
    'abstract_only': False,           # 是否仅搜索摘要
    'search_mode': 'all'             # 搜索模式：'all'(任意关键词匹配), 'any'(所有关键词都要匹配)
}

# arXiv 分类按研究主题分组。arXiv 没有单独的“电子显微学”分类，相关论文
# 通常分散在材料、凝聚态、仪器、应用物理、电子光学和数据分析等分类中。
CATEGORY_GROUPS = {
    # 材料与凝聚态物理核心分类
    'materials_and_condensed_matter': [
        "cond-mat.mtrl-sci",  # 材料科学
        "cond-mat.mes-hall",  # 介观与纳米物理、电子输运
        "cond-mat.str-el",    # 强关联电子系统
        "cond-mat.supr-con",  # 超导物理
        "cond-mat.dis-nn",    # 无序、缺陷与非均匀体系
        "cond-mat.soft",      # 软物质与复杂材料
        "cond-mat.other",     # 其他凝聚态交叉研究
    ],
    # 电子显微镜、探测器和电子光学相关方法
    'electron_microscopy_and_instrumentation': [
        "physics.ins-det",    # 仪器与探测器：显微镜、相机、谱仪
        "physics.app-ph",     # 应用物理：显微表征与原位实验
        "physics.optics",     # 电子光学、相位恢复与叠层成像
    ],
    # 原子尺度材料、谱学和多尺度模拟
    'atomic_scale_materials_and_simulation': [
        "physics.chem-ph",    # 化学物理、EELS/EDS 相关谱学
        "physics.atm-clus",   # 原子、分子团簇和纳米结构
        "physics.comp-ph",    # 计算物理与显微图像/衍射模拟
    ],
    # 4D-STEM、电子断层成像和显微数据分析
    'microscopy_data_analysis': [
        "physics.data-an",    # 物理数据分析与机器学习
        "eess.IV",            # 图像重建、去噪和逆问题
    ],
}

# 保持现有客户端需要的扁平列表格式，并按分组定义顺序去重。
CATEGORIES = list(dict.fromkeys(
    category
    for group in CATEGORY_GROUPS.values()
    for category in group
))

# 搜索查询配置，用OR或用AND连接关键词，或者没有关键词也可以留空
# QUERY = "nickelate OR cuprate"   # 搜索包含关键词nickelate或cuprate,并且在CATEGORIES中的所有文献
# QUERY = "nickelate AND cuprate"   # 搜索包含关键词nickelate和cuprate,并且在CATEGORIES中的所有文献
QUERY = ""     # 搜索CATEGORIES中的所有文献

# 语言模型API配置
LLM_CONFIG = {
    'api_key': os.getenv('LLM_API_KEY', 'YOUR_API_HERE'),
    'base_url': os.getenv('LLM_BASE_URL', 'https://api.agnes-ai.cn/v1'),
    'model': os.getenv('LLM_MODEL', 'agnes-2.5-flash'),
    'temperature': 0.5,           # 温度参数
    'max_output_tokens': 32648,   # 对应 Chat Completions 的 max_tokens
    'top_p': 0.8,                 # Top P 参数
    'retry_count': 3,             # API 调用失败时的重试次数
    'retry_delay': 2,             # 重试间隔（秒）
    'timeout': 300,               # API 请求超时时间（秒）
}

# 研究技术方法分类配置：对已生成的中文摘要进行理论/计算/实验多标签分类。
METHOD_CLASSIFICATION_CONFIG = {
    'enabled': True,
    'batch_size': 40,             # 单次分类的论文数量
    'confidence_threshold': 0.60, # 低于阈值时标记为“未判定”
    'response_retries': 2,        # JSON 格式或枚举校验失败时的修复次数
    'max_tokens': 4096,
    'model': os.getenv('METHOD_CLASSIFICATION_MODEL') or None,
    'backfill_existing': True,    # 自动补分类数据目录中的历史摘要
}

# 输出配置
OUTPUT_DIR = "data"
LAST_RUN_FILE = "last_run.json"  # 存储上次运行的信息
