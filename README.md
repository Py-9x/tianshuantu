# 天枢安途——高原探险全周期生命智能监护平台
高原探险整个超旧生奛阈探雕了解住怎粗茸的一个整体恰网（行前规划 / 行中监护 / 行后回顾）

## 重要说明（请先看）
1. **部署环境以百度飞桨 AI Studio 为准**  
本项目已按 AI Studio 场景调试，优先使用 `app.streamlit.py` 启动。

2. **不要轻易改动 Streamlit 版本**  
当前代码兼容目标为 **Streamlit 1.13.0**。  
如果升级到高版本，部分 API 行为可能变化（如 `rerun`、组件渲染、样式选择器），会导致页面错位或刷新异常。

3. **百度地图 AK 额度可能耗尽**  
当额度不足或网络不稳定时，真实地图可能无法显示。  
系统已提供降级方案（如离线轨迹图/模拟轨迹图），不会影响核心流程演示。

---

## 项目定位
“天涯守望”聚焦单人户外探险安全，形成完整闭环：
- **行前规划**：目的地 + 时间窗口 + 天气 + AI 结构化建议
- **行中监护**：体征模拟/采集、风险判定、动态行动建议、SOS 调度
- **行后回顾**：归档行程复盘、体征趋势、高风险事件、AI 总结报告

---

## 目录结构
```text
.
├─ app.streamlit.py         # 推荐入口（AI Studio）
├─ app.py                   # 兼容入口（本地调试）
├─ config.py                # 密钥与路径配置
├─ requirements.txt
├─ models/
│  ├─ db.py                 # SQLite 读写
│  ├─ schemas.py            # 建表 SQL
│  └─ lstm/                 # LSTM 推理相关文件
├─ services/
│  ├─ ai_service.py         # 文心大模型调用与兜底
│  ├─ baidu_api.py          # 地图/天气 API 封装
│  ├─ satellite.py          # 卫星链路调度模拟
│  └─ lstm_risk.py          # LSTM 风险推理
├─ views/
│  ├─ planning.py
│  ├─ monitoring.py
│  └─ retrospective.py
└─ static/
   └─ style.css             # 全局样式
```

---

## 运行方式

### 1) 安装依赖
```bash
pip install -r requirements.txt
```

### 2) 推荐启动（AI Studio）
```bash
streamlit run app.streamlit.py
```

### 3) 本地兼容启动
```bash
streamlit run app.py
```

默认访问地址：
`http://localhost:8501`

---

## 配置说明
请在 `config.py` 中配置以下关键项：
- `BAIDU_AK`：百度地图 API Key
- `AI_STUDIO_TOKEN`：文心模型访问 Token
- `DB_PATH`：SQLite 文件路径

---

## 版本约束（强烈建议固定）
- Python：建议 3.9/3.10
- Streamlit：**1.13.0**
- 其他依赖按 `requirements.txt`

> 若你必须升级 Streamlit，请先完整回归三页流程（登录、行前、行中、行后），尤其检查自动刷新和样式覆盖。

---

## 三页面能力概览

### 行前规划（`views/planning.py`）
- 目的地地理编码、天气窗口筛选、AI 出行建议
- 结果持久化到 `adventures` / `reports`
- 创建 adventure 时写入用户选定起始日期（提升时间真实性）

### 行中监护（`views/monitoring.py`）
- 体征窗口更新与风险评估（规则 + 可选 LSTM）
- 动态 AI 行动建议、定位/天气、SOS 队列
- 支持“结束行程并归档”，归档后停止写入体征

### 行后回顾（`views/retrospective.py`）
- 已归档行程列表与详情
- 体征曲线 + 高风险事件列表
- 轨迹显示模式：
  - 真实地图（依赖百度额度）
  - 离线轨迹图（matplotlib，基于真实坐标，不依赖外部底图）
- AI 报告自动生成（无报告时）+ 手动刷新

---

## 地图不可用时的预期表现
出现以下任一情况都可能导致真实地图失败：
- 百度 AK 配额用尽
- 网络波动
- API 返回限流/鉴权错误

系统处理策略：
- 行中：提示失败并保留坐标，不阻断监护流程
- 行后：自动降级到离线轨迹图（白底折线+起终点）

这属于**预期降级**，不是程序崩溃。

---

## 常见问题（FAQ）

### Q1：为什么页面报错 `sqlite3.Row has no attribute get`？
A：说明代码里把 `sqlite3.Row` 当 `dict` 用了。  
当前版本已在行后页通过兼容读取函数处理；若你自行改代码，注意统一访问方式。

### Q2：为什么看到“当前未知-日期”的历史行程？
A：这是早期未从行前联动目的地时产生的历史脏数据。  
可在行后页勾选“隐藏未知行程”，或清理数据库旧记录。

### Q3：AI 报告为什么和预期不一致？
A：文心调用失败时会走模板兜底。请先检查 `AI_STUDIO_TOKEN` 是否有效。

---

## 免责声明
本项目用于教学/竞赛演示，不构成医疗诊断或专业救援指令。
