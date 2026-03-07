# 🚀 Streamlit Cloud 部署 - 快速参考

## 5 分钟快速部署

### 第1步：GitHub（5分钟）

```bash
# 在项目根目录运行
cd D:\tianyashouwang\tianya_new_9

git init
git add .
git remote add origin https://github.com/YOUR_USERNAME/tianya-skyguard.git
git branch -M main
git commit -m "Initial commit"
git push -u origin main
```

**需要做什么**：
1. 创建 GitHub 仓库：https://github.com/new
2. 替换 `YOUR_USERNAME` 为你的 GitHub 用户名仓库名为 `tianya-skyguard`
3. 上面的命令执行完即可

### 第2步：Streamlit Cloud（3分钟）

1. 登录 https://streamlit.io/cloud
2. 点击 **"New app"**
3. 选择：
   - **Repository**: `YOUR_USERNAME/tianya-skyguard`
   - **Main file path**: `streamlit_app.py`
   - **Branch**: `main`
4. 点击 **"Deploy!"**

### 第3步：配置 Secrets（1分钟）

部署完成后：
1. 点击右上角 **"☰" → "Settings"**
2. 在 "Secrets" 中粘贴：

```toml
AI_STUDIO_TOKEN = "sk-xxxxx..."
BAIDU_AK = "xxx..."
QWEATHER_KEY = "xxx..."
```

3. 点击 **"Save"**

---

## ✅ 成功标志

- [ ] 应用 URL 可访问（https://your-username-xxx.streamlit.app/）
- [ ] 能正常登录
- [ ] 四个页签都能打开
- [ ] 没有红色错误信息

---

## ❌ 常见错误速查

| 症状 | 解决 |
|------|------|
| **ModuleNotFoundError** | 查看 Logs → 缺少模块 → 加到 requirements.txt |
| **AttributeError** | 查看 Logs → 导入错误 → 检查 streamlit_app.py |
| **API 调用失败** | 检查 Secrets 中的 keys 是否正确 |
| **数据每次都丢失** | 正常，SQLite 不持久化，需要用云数据库 |
| **部署超时** | requirements.txt 文件太大，删除不必要的包 |

---

## 📚 详细文档

完整的部署指南请看：[README_DEPLOYMENT.md](README_DEPLOYMENT.md)

---

## 💬 需要帮助？

- 遇到错误 → 查看 Cloud Dashboard 的 "Logs"
- 还是不懂 → 查看 README_DEPLOYMENT.md 的对应章节
- 还是没解决 → 联系技术支持或在 GitHub Issues 中提问
