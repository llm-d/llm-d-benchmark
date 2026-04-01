"""Setup configuration for LLM-D Stack Discovery Tool."""

from pathlib import Path

from setuptools import setup, find_packages

# Read the README file
readme_path = Path(__file__).parent / "README.md"
long_description = readme_path.read_text() if readme_path.exists() else ""

# Read requirements
requirements_path = Path(__file__).parent / "requirements.txt"
requirements = []
if requirements_path.exists():
    requirements = [
        line.strip()
        for line in requirements_path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]

setup(
    name="llm-d-stack-discovery",
    version="0.1.0",
    author="LLM-D Benchmark Team",
    description="A tool for discovering LLM-D stack configuration from OpenAI endpoints",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/llm-d/llm-d-benchmark",
    packages=find_packages(),
    install_requires=requirements,
    python_requires=">=3.11",
    entry_points={
        "console_scripts": [
            "llm-d-discover=llm_d_stack_discovery.cli:main",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "License :: OSI Approved :: Apache Software License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    keywords="llm-d kubernetes openshift discovery configuration",
)
