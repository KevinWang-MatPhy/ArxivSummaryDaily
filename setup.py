from setuptools import setup, find_packages

setup(
    name="arxivsummary",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "arxiv",
        "python-dotenv",
    ],
    entry_points={
        'console_scripts': [
            'arxivsummary=src.cli:main',
            'arxivclassify=src.classify_cli:main',
            'arxivsite=src.site_manager:main',  # 添加新的命令行入口点
        ],
    },
    author="DongZehao",
    description="A tool for generating daily summaries of arXiv papers",
    python_requires=">=3.9",
)
