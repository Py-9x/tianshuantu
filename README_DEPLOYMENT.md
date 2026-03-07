# 🚀 Streamlit Cloud 云原生部署指南

> **项目**：云陆卫士 - 高原失能预防医学决策系统  
> **目标**：完整云原生部署（数据库 + API + 模型都在云端）

---

## 📋 部署前检查清单

- [x] 项目结构就绪
- [x] `streamlit_app.py` 已创建（Streamlit Cloud 入口）
- [x] `requirements.txt` 已优化
- [x] `.streamlit/config.toml` 已配置
- [x] `.streamlit/secrets.toml` 示例已创建
- [ ] GitHub 仓库已创建
- [ ] GitHub Secrets 已配置
- [ ] Streamlit Cloud 账户已创建
- [ ] 云数据库（可选）已配置

---

## 第一步：准备 GitHub 仓库

### 1.1 创建 GitHub 仓库

1. 登录 [github.com](https://github.com)
2. 点击 **"+" → "New repository"**
3. 配置：
   - **Repository name**: `tianya-skyguard` 或 `cloud-altitude-guard`
   - **Description**: `High-altitude failure prevention medical decision system | 高原失能预防医学决策系统`
   - **Visibility**: **Public** （Streamlit Cloud 免费版需要 public 仓库）
   - **Initialize repository**: 勾选 "Add a README.md"
4. 点击 **"Create repository"**

### 1.2 添加 .gitignore

在仓库根目录创建 `.gitignore`：

```
__pycache__/
*.pyc
*.pyo
*.db
.env
.streamlit/secrets.toml
tianya.db
*.pdiparams
*.pdparams
.pytest_cache/
.DS_Store
```

### 1.3 提交本地代码到 GitHub

```bash
cd D:\tianyashouwang\tianya_new_9

# 初始化 git（如果还没）
git init

# 添加remote
git remote add origin https://github.com/YOUR_USERNAME/tianya-skyguard.git

# 添加所有文件
git add .

# 首次提交
git commit -m "Initial commit: Cloud-native deployment ready"

# 上传到 GitHub
git branch -M main
git push -u origin main
```

**错误排除**：
| 错误 | 原因 | 解决 |
|------|------|------|
| `fatal: not a git repository` | 没初始化 | 运行 `git init` |
| `Authentication failed` | 密码错误或需要 token | 使用 Personal Access Token（PAT）而非密码 |
| `rejected` | 仓库冲突 | 运行 `git pull origin main` |

---

## 第二步：配置 Streamlit Cloud

### 2.1 连接 GitHub 到 Streamlit

1. 登录 [Streamlit Cloud](https://streamlit.io/cloud)
2. 点击 **"New app"**
3. 选择：
   - **GitHub account**: 选择你的账户
   - **Repository**: `YOUR_USERNAME/tianya-skyguard`
   - **Branch**: `main`
   - **Main file path**: `streamlit_app.py`
4. 点击 **"Deploy!"**

### 2.2 配置 Secrets（关键！）

1. 部署完成后，点击右上角 **"☰" → "Settings"**
2. 找到 **"Secrets"** 部分
3. 在文本框中粘贴：

```toml
[theme]
primaryColor = "#00C6FF"
backgroundColor = "#0A121E"

AI_STUDIO_TOKEN = "sk-xxxxx..."
BAIDU_AK = "xxx..."
QWEATHER_KEY = "xxx..."
```

4. 点击 **"Save"**

> ⚠️ **重要**：不要把 secrets 提交到 GitHub，只提交 `.streamlit/secrets.toml.example`

### 2.3 监控部署日志

在 Cloud Dashboard 中：
- ✅ 绿色 → 部署成功
- 🟡 黄色 → 部署中
- ❌ 红色 → 部署失败（点击查看日志）

常见错误：
```
ModuleNotFoundError: No module named 'xxx'
  → 检查 requirements.txt 是否包含该包

ImportError: cannot import name 'xxx'
  → 检查文件结构，确保模块路径正确

TimeoutError
  → 依赖太大（特别是 PaddlePaddle），需要精简
```

---

## 第三步：云原生优化

### 3.1 问题：SQLite 在云端不持久化

**当前状态**：`tianya.db` 在本地存储，云端重启后丢失

**解决方案**：迁移到云数据库

#### 选项A：Supabase（PostgreSQL）- 推荐

```bash
# 1. 创建自由层账户 → https://supabase.com
# 2. 新建项目 → 获得 DATABASE_URL

# 3. 在 .streamlit/secrets.toml 中添加：
DATABASE_URL = "postgresql://user:password@db.supabase.co:5432/postgres"
USE_CLOUD_DB = true

# 4. 修改 models/db.py 顶部：
import os
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///tianya.db")
```

#### 选项B：MongoDB Atlas（NoSQL）

```bash
# 1. 创建账户 → https://www.mongodb.com/cloud
# 2. 获得连接字符串：mongodb+srv://...

# 3. 在 secrets 中添加：
MONGO_URI = "mongodb+srv://..."
```

### 3.2 问题：LSTM 模型文件过大

**当前**：`models/lstm/*.pdparams` 在仓库中（>200MB）

**解决**：上传到 Hugging Face Hub

```bash
# 1. 创建账户 → https://huggingface.co
# 2. 创建 Model Repo → `your-username/tianya-lstm-model`
# 3. 上传模型文件：
git clone https://huggingface.co/your-username/tianya-lstm-model
cp models/lstm/*.pdparams tianya-lstm-model/
cd tianya-lstm-model
git add . && git commit -m "LSTM model" && git push

# 4. 修改 services/lstm_risk.py：
from huggingface_hub import hf_hub_download
model_path = hf_hub_download(
    repo_id="your-username/tianya-lstm-model",
    filename="vitals_lstm_model.pdparams"
)
```

### 3.3 问题：实时数据采集无法工作

**当前**：需要用户设备上的传感器（高心率表、血氧仪、温度计）

**Streamlit Cloud 环境**：
- ❌ 不能调用 `streamlit_geolocation`（要求用户授权）
- ❌ 不能读取真实传感器数据
- ✅ 可以用演示数据 + 用户手动输入

**建议**：在 `views/monitoring.py` 中添加 fallback

```python
# 如果无法获取实时数据，使用演示模式
if CLOUD_ENVIRONMENT:
    vitals = generate_demo_vitals()  # 演示数据
else:
    vitals = get_sensor_data()  # 实时数据
```

---

## 第四步：上线前测试清单

### 本地测试（Streamlit CLI）

```bash
# 1. 启动本地服务
streamlit run streamlit_app.py

# 2. 测试所有页面
#    - 登录页面
#    - 行前规划页面
#    - 行中监护页面
#    - 行后回顾页面
#    - 用户中心页面

# 3. 检查 API 调用
#    - 文心大模型 API（生成行动建议）
#    - 百度地图 API（显示地图）
#    - 天气 API（获取预报）

# 4. 检查数据持久化
#    - 创建行程 → 刷新页面 → 确认数据仍存在
```

### 云端测试（Streamlit Cloud）

```bash
# 部署到 Cloud 后访问：
https://your-username-tianya-skyguard.streamlit.app/

# 测试项目
✓ 登录功能
✓ 数据库读写（演示数据能否正常显示）
✓ API 调用（是否能生成建议）
✓ 图表渲染（Plotly 是否正确显示）
✓ 响应速度（是否存在超时）
```

---

## 第五步：监控与维护

### 监控日志

```bash
# 在 Streamlit Cloud Dashboard 中：
1. 点击你的应用
2. 点击 "Logs" 查看实时日志
3. 错误信息会自动记录
```

### 自动重新部署

```bash
# 每次 push 到 main 分支时自动重新部署
git add .
git commit -m "Fix: ActionGenerator prompt"
git push origin main
# → Streamlit Cloud 自动检测并重新部署
```

### 定期检查

- [ ] 每周检查错误日志
- [ ] 每月检查数据库使用量
- [ ] 每月检查 API 调用量（防超费）
- [ ] 定期更新依赖（`pip list --outdated`）

---

## 常见问题排除

### Q1：部署成功但应用打不开
```
错误：Streamlit is not responding
原因：streamlit_app.py 有语法错误或导入失败
解决：查看 Cloud Logs，修复错误后重新 push
```

### Q2：API 调用失败
```
错误：AI调用失败 或 地图加载失败
原因：Secrets 未正确配置 或 API 额度用尽
解决：
  1. 检查 .streamlit/secrets.toml 中的 keys 是否正确
  2. 检查 API 供应商的配额
```

### Q3：数据丢失
```
错误：每次运行应用时行程数据都消失
原因：使用 SQLite，Cloud 环境不持久化文件
解决：迁移到云数据库（Supabase 或 MongoDB）
```

### Q4：部署超时
```
错误：Timeout waiting for dependencies
原因：requirements.txt 中的包太大或下载慢
解决：
  1. 删除不必要的包
  2. 换用更轻量的替代方案
  3. 增加超时时间（不推荐）
```

---

## 📱 部署完成后

### 分享你的应用

```
应用 URL：https://your-username-tianya-skyguard.streamlit.app/

分享方式：
- 复制链接发给测试人员
- 在 GitHub README 中添加链接
- 在简历/竞赛中心中宣传
```

### 竞赛提交

对于中国大学生计算机设计大赛：
```
作品链接：https://your-username-tianya-skyguard.streamlit.app/
源代码：https://github.com/your-username/tianya-skyguard
演示视频：（可选，上传到 YouTube/Bilibili）
```

---

## 🎯 下一步

1. **创建 GitHub 仓库**（按第一步）
2. **连接 Streamlit Cloud**（按第二步）
3. **配置 API Secrets**（按第二步 2.2）
4. **优化云原生架构**（可选，按第三步）
5. **进行完整测试**（按第四步）

有任何问题，查看对应的"常见问题排除"部分。

---

**最终效果**：
```
你的云陆卫士系统在线访问
   ↓
评委/用户点击链接
   ↓
立即看到完整的高原失能预防系统演示
   ↓
🏆 竞赛评分！
```

祝部署顺利！🚀
