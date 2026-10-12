# Lemon 员工签到与行为分析系统

这是一个 Flask 员工签到、请假审核、行为分析和刷脸打卡系统。项目默认支持 SQLite 本地开发，也支持通过 SQLAlchemy + PyMySQL 连接 MySQL，适合上传到 GitHub 后在 Windows + PyCharm 中运行。

## 功能概览

- 首页刷脸打卡
- 管理员登录与员工资料维护
- 员工请假申请与管理员审核
- 员工行为分析
- 人脸模板管理
- SQLite / MySQL 可切换数据库配置

## Windows + PyCharm 运行步骤

1. 克隆项目：

```bat
git clone <your-repo-url>
cd lemon
```

2. 用 PyCharm 打开项目目录。

3. 创建虚拟环境：

在 PyCharm 中打开 `Settings -> Project -> Python Interpreter`，选择 `Add Interpreter`，推荐创建项目内 `.venv`。

也可以在终端中创建：

```bat
python -m venv .venv
.venv\Scripts\activate
```

4. 安装依赖：

```bat
python -m pip install -r requirements.txt
```

5. 配置环境变量：

复制 `.env.example` 为 `.env`：

```bat
copy .env.example .env
```

开发阶段可先使用 SQLite：

```env
DB_BACKEND=sqlite
DATABASE_URL=
```

如果使用 MySQL，请先创建数据库：

```sql
CREATE DATABASE lemon CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

然后在 `.env` 中配置：

```env
DATABASE_URL=mysql+pymysql://username:password@127.0.0.1:3306/lemon?charset=utf8mb4
```

或使用拆分配置：

```env
DB_BACKEND=mysql
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_DATABASE=lemon
MYSQL_USER=root
MYSQL_PASSWORD=123456
```

如果需要验证行为分析的大模型调用，请在 `.env` 中继续配置：

```env
DEEPSEEK_API_KEY=your_deepseek_api_key_here
DEEPSEEK_BASE_URL=https://api.deepseek.com
LONG_ANALYSIS_MODEL=deepseek-reasoner
```

6. 启动项目：

在 PyCharm 中右键运行 `app.py`，或运行：

```bat
run.bat
```

访问：

```text
http://127.0.0.1:5000
```

## macOS / Linux 运行步骤

```bash
git clone <your-repo-url>
cd lemon
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
./run.sh
```

如果 `run.sh` 没有执行权限：

```bash
chmod +x run.sh
./run.sh
```

## 数据库配置说明

数据库连接优先读取 `DATABASE_URL`。如果没有配置 `DATABASE_URL`，则根据 `DB_BACKEND` 回退：

- `DB_BACKEND=sqlite`：使用 `instance/lemon.db`
- `DB_BACKEND=mysql`：使用 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD` 组装 MySQL 连接

SQLite 示例：

```env
DATABASE_URL=sqlite:///instance/lemon.db
```

MySQL 示例：

```env
DATABASE_URL=mysql+pymysql://username:password@127.0.0.1:3306/lemon?charset=utf8mb4
```

应用启动时会以增量兼容方式检查核心表和缺失字段，不会删除现有数据库文件，也不会执行删库重建。

## 从 SQLite 迁移到 MySQL

迁移脚本：

```text
scripts/migrate_sqlite_to_mysql.py
```

脚本会：

- 在 MySQL 中检查并创建缺失表结构
- 从 SQLite 读取已有数据
- 按主键增量导入到 MySQL
- 已存在主键的数据会跳过，不会删除或清空 MySQL 数据

支持的数据表：

- `admin`
- `employee`
- `attendance`
- `leave_request`
- `analysis`
- `employee_face_profile`

仅创建 MySQL 表结构：

```bash
DATABASE_URL="mysql+pymysql://username:password@127.0.0.1:3306/lemon?charset=utf8mb4" \
python scripts/migrate_sqlite_to_mysql.py --create-tables-only
```

导入 SQLite 数据到 MySQL：

```bash
DATABASE_URL="mysql+pymysql://username:password@127.0.0.1:3306/lemon?charset=utf8mb4" \
python scripts/migrate_sqlite_to_mysql.py --sqlite-path instance/lemon.db
```

Windows PowerShell 示例：

```powershell
$env:DATABASE_URL="mysql+pymysql://username:password@127.0.0.1:3306/lemon?charset=utf8mb4"
python scripts/migrate_sqlite_to_mysql.py --sqlite-path instance/lemon.db
```

## 上传目录和本地文件

项目启动时会自动创建：

- `instance/`
- `uploads/`
- `uploads/face_templates/`

这些目录中的运行期数据默认不会上传到 GitHub。仓库只保留 `.gitkeep` 占位文件。

## 默认账号

管理员账号：

```text
用户名：root
密码：123456
```

员工账号由管理员录入员工资料后生成，员工打卡统一使用首页刷脸打卡。

## 验证方法

```bash
python -m compileall app.py models.py routes services scripts
python app.py
```

浏览器打开：

```text
http://127.0.0.1:5000
```

建议验证：

- 首页刷脸打卡页面可访问
- 管理员可登录后台
- 员工列表、人脸模板管理、请假审核页面可打开
- 员工请假、资料、修改密码页面可打开
- MySQL 模式下配置 `DATABASE_URL` 后应用可启动

## 测试数据导入与行为分析验证

项目提供了 CSV 考勤数据导入脚本。脚本只向现有 `attendance` 表追加记录，不会删除表、重建数据库或覆盖员工账号。

```bash
python scripts/import_attendance_csv_to_sqlite.py attendance.csv --db instance/lemon.db
```

如果 CSV 中的测试工号与当前数据库员工工号不一致，可按当前数据库员工工号顺序重映射后导入：

```bash
python scripts/import_attendance_csv_to_sqlite.py attendance.csv --db instance/lemon.db --remap-current
```

行为分析大模型验证脚本：

```bash
python scripts/verify_analysis_llm.py --employee-id 999999991 --employee-id 999999992
```

接口可用时，脚本会生成长期和短期分析记录，并在 `analysis_results/` 下保存每个员工的 JSON 结果。该目录属于运行期输出，默认不会提交到 GitHub。

---

## 支持

如果这个项目帮到了你，可以请我喝杯咖啡（完全自愿）：**https://ko-fi.com/colome**

更多项目：**https://github.com/SourceAn**
