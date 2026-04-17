# Paper-Engine

[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-App-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![Deploy GitHub Pages](https://github.com/liaohaoliang2003/Paper-Engine/actions/workflows/pages.yml/badge.svg)](https://github.com/liaohaoliang2003/Paper-Engine/actions/workflows/pages.yml)
[![License](https://img.shields.io/github/license/liaohaoliang2003/Paper-Engine)](./LICENSE)
[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-Live-0A7EA4?logo=github&logoColor=white)](https://liaohaoliang2003.github.io/Paper-Engine/)

Paper-Engine 是一个本地优先的论文工作台，整合了“今日论文推荐”和“深度论文研读”两条主流程，面向 AI4Research 场景提供可执行、可追溯、可落盘的报告生产能力。

## 核心能力

- 今日推荐：基于 `aminer-daily-paper` skill 生成候选论文并筛选 Top-K。
- 深度研读：基于 `paper-deep-reading` 生成 7 章结构化报告，强制证据锚点与不确定性标注。
- 报告落盘：同目录输出 `*_report.md` + `*_report.html`，HTML 支持轻量交互组件。
- 本地控制台：`apps/research-console` 提供 Streamlit 一体化 UI（分步模式 + 一键流水线）。

## 快速开始

```bash
cd apps/research-console
pip install -r requirements.txt
streamlit run app.py
```

## 项目结构

```text
apps/research-console/                 # 一体化 Web 控制台
skills/paper-deep-reading/             # 研读 skill 定义、references、scripts
docs/prompts/                          # 研读主 Prompt
scripts/                               # 仓库级脚本与兼容入口
site/                                  # GitHub Pages 静态站点
```

## GitHub Pages

- 访问地址：https://liaohaoliang2003.github.io/Paper-Engine/
- 部署方式：`main` 分支触发 GitHub Actions 工作流 `Deploy GitHub Pages`
- 站点来源：仓库 `site/` 目录静态文件构建产物
