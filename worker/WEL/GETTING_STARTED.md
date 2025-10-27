# WEL v2.0 快速开始

欢迎使用 **WEL (Workflow Execution Language)** v2.0！

## 🎯 30秒快速了解

WEL 是一个将 Planner 规划转换为可执行工作流的框架，具有：
- ✅ 六层架构设计
- ✅ 显式数据流管理  
- ✅ 完整质量保证
- ✅ 全面可观测性

## 📚 文档导航

### 从这里开始

1. **完整文档** → [`WEL_README.md`](WEL_README.md) (939行, 27KB)
   - 核心概念、架构设计、API 参考、最佳实践
   - **推荐首先阅读**

2. **文件导览** → [`FILES_GUIDE.md`](FILES_GUIDE.md) (308行, 12KB)
   - 快速查找各个文件的作用
   - 按需查找功能和组件

3. **使用示例** → [`example_usage.py`](example_usage.py)
   - 可运行的完整示例代码
   - 麻醉药物分析场景演示

## 🚀 5分钟运行第一个示例

```bash
# 1. 进入项目目录
cd /data/agent_platform

# 2. 运行内置示例
python -m worker.WEL.example_usage

# 3. 运行测试案例（空气湿度与银屑病相关性分析）
python test/test_humidity_psoriasis_analysis.py
```

## 💻 快速使用代码

```python
import asyncio
from worker.WEL import WELGenerator, IntentType

async def main():
    # 1. 创建生成器
    generator = WELGenerator()
    
    # 2. 准备 Planner 输出（5个部分）
    requirement = {'task_type': '数据分析', 'goal': '...'}
    experience = {'success': True, 'experience': '...'}
    knowledge = {'success': False, 'knowledge': {}}
    tools = {'success': True, 'tools': [...]}
    tasks = {'scenario': '...', 'tasks': [...]}
    
    # 3. 生成 WEL 文档
    wel_doc = await generator.generate_from_planner(
        requirement, experience, knowledge, tools, tasks,
        intent_type=IntentType.DATA_ANALYSIS,
        intent_confidence=0.9
    )
    
    # 4. 使用生成的文档
    print(wel_doc.to_json())  # 输出 JSON
    print(f"动作数: {wel_doc.metrics.action_count}")
    print(f"置信度: {wel_doc.metrics.confidence_score}")

asyncio.run(main())
```

## 📖 学习路径

### 初级（1小时）
1. 阅读 `WEL_README.md` 的"简介"和"核心概念"部分
2. 运行 `example_usage.py` 查看输出
3. 阅读 `FILES_GUIDE.md` 了解文件结构

### 中级（3小时）
1. 阅读完整 `WEL_README.md`
2. 查看 `action_components.py` 了解六层架构
3. 研究 `wel_generator.py` 的生成逻辑
4. 运行并修改测试案例

### 高级（1天）
1. 深入所有源代码
2. 理解 Planner 上下文的设计
3. 创建自定义场景的 WEL 文档
4. 扩展框架功能

## 🔍 常见问题

**Q: 从哪里开始？**
→ 阅读 [`WEL_README.md`](WEL_README.md)

**Q: 如何找到特定功能？**
→ 查看 [`FILES_GUIDE.md`](FILES_GUIDE.md) 的"按需查找"表格

**Q: 有示例代码吗？**
→ 运行 `example_usage.py` 或查看 `test/test_humidity_psoriasis_analysis.py`

**Q: 支持哪些动作类型？**
→ 查看 `enums.py` 中的 `ActionType`（共21种）

**Q: 如何添加断言？**
→ 参考 [`WEL_README.md`](WEL_README.md) 的"常见问题"章节

## 📦 目录结构

```
WEL/
├── 📘 WEL_README.md              ← 完整文档（必读）
├── 📋 FILES_GUIDE.md             ← 文件导览
├── 🚀 GETTING_STARTED.md         ← 本文件
│
├── 🔧 核心模块
│   ├── wel_generator.py          ← 主入口
│   ├── wel_document.py           ← 文档类
│   └── wel_action.py             ← 动作类
│
├── 📦 组件模块  
│   ├── enums.py
│   ├── planner_context.py
│   ├── action_components.py
│   └── document_components.py
│
└── 📖 示例
    └── example_usage.py          ← 可运行示例
```

## 🎓 推荐阅读顺序

1. ✅ 本文件（5分钟）- 了解概况
2. ✅ [`WEL_README.md`](WEL_README.md) 前3章（15分钟）- 核心概念
3. ✅ 运行 `example_usage.py`（5分钟）- 实践体验
4. ✅ [`FILES_GUIDE.md`](FILES_GUIDE.md)（10分钟）- 文件结构
5. ✅ [`WEL_README.md`](WEL_README.md) 完整版（1小时）- 深入理解

## 💡 提示

- 所有代码都有详细的文档字符串
- 使用 Python type hints 便于 IDE 自动补全
- 示例代码可以直接运行
- 测试案例提供真实场景参考

---

**开始你的 WEL 之旅吧！** 🚀

有问题？查看 [`WEL_README.md`](WEL_README.md) 或阅读源代码。

*Agent Platform Team | 2025*

