from setuptools import setup, find_packages

setup(
    name="agent-memory",
    version="0.1.0",
    description="Save 60-90% on LLM token costs with intelligent memory compression for multi-agent systems",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="agent-memory contributors",
    license="MIT",
    url="https://github.com/Keshab0310/agent-memory",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "anthropic>=0.52.0",
    ],
    extras_require={
        "vector": ["chromadb>=0.5.0"],
        "local": ["openai>=1.0.0"],
        "mcp": ["mcp>=1.0.0"],
        "all": ["chromadb>=0.5.0", "openai>=1.0.0", "mcp>=1.0.0"],
        "dev": ["pytest>=8.0", "chromadb>=0.5.0", "openai>=1.0.0"],
    },
    entry_points={
        "console_scripts": [
            "agent-memory=src.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Software Development :: Libraries",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
)
