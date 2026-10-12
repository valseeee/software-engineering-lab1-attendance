# 签到系统

本项目是软件工程课程实验一的示例项目，使用 Python 实现命令行签到和记录查询。系统会校验空学号、空姓名，并阻止同一学号在同一天重复签到。

## 运行环境

- Python 3.12 或更高版本
- Git 2.47 或更高版本

## 使用方法

```powershell
python signin.py sign 20260001 张三
python signin.py list
python hello_ai.py
python -m unittest -v
```

签到数据保存在项目目录的 `records.json` 文件中。
