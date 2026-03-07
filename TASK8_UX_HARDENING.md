# Task 8: 用户友好的SOS错误处理 - 完实施

## 概述
将SOS应急系统的错误处理从**技术jargon转换为用户友好的可操作指导**，并修复Streamlit布局错误。

## 修复详情

### 修复1: 错误分类和上下文感知恢复
**文件**: `views/monitoring.py`
**新函数**: `_classify_error(error: Exception) -> str`

```python
def _classify_error(error: Exception) -> str:
    """
    分类技术错误，映射到用户可理解的场景
    返回值: "NETWORK_WEAK" | "BATTERY_LOW" | "LAYOUT_ERROR" | "SYSTEM_RESOURCE" | "UNKNOWN"
    """
```

**映射规则**:
- 包含 `network`, `socket`, `timeout`, `connection` → `NETWORK_WEAK`
- 包含 `battery`, `power`, `low` → `BATTERY_LOW`
- 包含 `columns`, `column context` → `LAYOUT_ERROR`
- 错误类型包含 `memory`, `recursion` → `SYSTEM_RESOURCE`
- 其他 → `UNKNOWN`

---

### 修复2: 弱信号和低电量情景指导
**文件**: `views/monitoring.py`
**新函数**: 
- `_get_weak_signal_guidance() -> List[Dict]`
- `_get_low_battery_guidance() -> List[Dict]`

**弱信号指导（优先级排序）**:
1. 📱 **举高手机** - 将手机举过头顶，屏幕朝上，面向开阔天空
   - 为什么：减少身体遮挡，增加卫星捕获概率
2. 🏃 **移动到开阔地** - 远离山体、建筑物、树木
   - 为什么：卫星信号无法穿透固体障碍
3. 🧘 **保持手机稳定** - 双手握持，手肘贴紧身体
   - 为什么：卫星连接需要3-5秒的稳定指向

**低电量指导**:
1. 🔋 **关闭不必要的功能** - 屏幕亮度、蓝牙、Wi-Fi
   - 为什么：延长电池寿命，确保SOS信号完整
2. 📍 **中断其他活动** - 停止导航、拍照、视频等
   - 为什么：优先保证应急通信

---

### 修复3: 用户感知的错误处理路由
**文件**: `views/monitoring.py`
**新函数**: `_handle_sos_error(error: Exception, env_status: Dict)`

**错误处理流程**:
```
异常发生
    ↓
_classify_error() → 获取错误类型
    ↓
根据错误类型路由:
    ├─ NETWORK_WEAK → 弱信号指导 (黄色) 
    ├─ BATTERY_LOW → 低电量指导 (红色)
    ├─ LAYOUT_ERROR → 静默日志 (蓝色，不显示用户)
    ├─ SYSTEM_RESOURCE → 资源释放指导
    └─ UNKNOWN → 备用通道通知
    ↓
_show_sos_status_card() → 用户友好显示
    ↓
后台日志 (技术信息供调试)
```

**环境状态传递**:
```python
env_status = {
    'signal': '弱' | '正常',      # 信号状态
    'battery': int,              # 电量百分比
    'retry_count': int,          # 重试次数
    'is_retrying': bool,         # 是否在重试中
    'retry_progress': float      # 重试进度 0.0-1.0
}
```

---

### 修复4: 容器布局替代columns（避免嵌套错误）
**文件**: `views/monitoring.py`
**新函数**: `_show_sos_status_card(title: str, guidance_steps: List[Dict], color: str, env_status: Dict)`

**问题**: Streamlit不允许在columns内部嵌套columns，会导致运行时错误
**解决方案**: 用`st.container()` + `st.markdown(HTML)` 替代原有st.columns方案

**卡片结构**:
```
┌─ st.container() ─────────────────────────────┐
│ ┌─ 标题区 ──────────────────────────────────┐│
│ │ 📡 信号较弱，正在优化...                   ││
│ │ 📡 信号: 弱  🔋 电量: 45%  🔄 重试: 2/5  ││
│ └────────────────────────────────────────────┘│
│ ┌─ 指导步骤 (HTML) ──────────────────────────┐│
│ │ 请立即执行（已按优先级排序）：             ││
│ │ 1. 📱 举高手机                            ││
│ │    将手机举过头顶...                       ││
│ │    💡 减少身体遮挡...                      ││
│ └────────────────────────────────────────────┘│
│ ┌─ 系统进度 ──────────────────────────────────┐│
│ │ ⚙️ 系统正在：                              ││
│ │ [████████░░░░░░░░░░░░░░] 50%              ││
│ └────────────────────────────────────────────┘│
└──────────────────────────────────────────────┘
```

---

### 修复5: 成功情况UI优化
**文件**: `views/monitoring.py`
**重构函数**: `_provide_feedback(success: bool, payload_level: PayloadLevel)`

**成功UI** (避免原有嵌套columns问题):
```
✅ 紧急求救信号已发送！
搜救队正在接收您的位置和生命体征信息。
保持手机信号稳定，继续传输中...

[📊 信号详情] (可展开)

现在该做什么？
1. 📱 保持手机举起，面向开阔天空
2. 🏃 如可能，移动到开阔地（远离树木、建筑）
3. 🧘 保持手机稳定，避免晃动

搜救队通常在 5-20分钟 内做出响应。
```

**失败UI**: 已转移至 `_handle_sos_error()` 处理

---

### 修复6: _send_sos 异常处理重构
**文件**: `views/monitoring.py`  
**从**: 第2354行
**改变**: 异常处理从 `st.error(error_msg)` 改为 `_handle_sos_error(error, env_status)`

**前**:
```python
except Exception as e:
    error_msg = f"❌ 数字生命信标发送失败: {str(e)}"
    st.error(error_msg)
    print(f"[SOS] ❌ 发送失败: {error_msg}")
    _provide_feedback(success=False, payload_level=PayloadLevel.FULL)
```

**后**:
```python
except Exception as e:
    print(f"[SOS] ❌ 发送失败: {str(e)}")
    
    env_status = {
        'signal': '弱' if not st.session_state.signal_available else '正常',
        'battery': int(st.session_state.battery_pct),
        'retry_count': st.session_state.get('sos_retry_count', 0),
        'is_retrying': st.session_state.get('sos_retry_scheduled', False),
        'retry_progress': 0.5
    }
    
    _handle_sos_error(e, env_status)
```

---

## 术语替换
将所有UI显示文本中的专业术语替换为用户理解的语言：

| 原术语 | 新术语 | 上下文 |
|--------|--------|--------|
| 数字生命信标 | 紧急求救信号 | UI消息、event日志 |
| 二进制编码 | 信息压缩 | 错误提示 |
| 提高天线 | 举高手机 | 弱信号指导 |
| 更换位置 | 移动到开阔地 | 弱信号指导 |
| [保留] | [保留] | 代码注释、内部日志 (prefix: `[SOS]`) |

**替换列表**:
- ✅ Line 1723: "⚠️ 二进制编码失败" → "⚠️ 信息压缩失败"
- ✅ Line 2120: "❌ SOS信标发送失败" → "❌ 紧急求救信号发送失败"
- ✅ Line 2121: "提高天线" → "举高手机"
- ✅ Line 2387: "数字生命信标已发出" → "紧急求救信号已发送"

---

## 辅助函数
新增内部支持函数：

### `_schedule_recovery_action(error_type: str)`
根据错误类型安排自动恢复策略（如10秒后重试）

### `_log_internal_error(error: Exception)`
后台记录代码级错误，不显示给用户（用于调试）

---

## 会话状态扩展
SOS错误处理使用的新session变量：
```python
st.session_state.sos_retry_count       # 重试计数
st.session_state.sos_retry_scheduled   # 是否安排了重试
st.session_state.sos_retry_time        # 下次重试时间戳
st.session_state.sos_auto_retry        # 自动重试标志
```

---

## UI配色方案
- 🟢 **成功/安全** (#10B981): 紧急求救信号已发送
- 🟡 **注意/弱信号** (#F59E0B): 信号较弱，正在优化
- 🔴 **危险/低电量** (#EF4444): 电量紧张，启动应急模式
- 🔵 **信息/系统** (#00C6FF): 系统正在优化...

---

## 验证清单
- ✅ Python语法通过 (`py_compile`)
- ✅ 新函数 _classify_error, _get_weak_signal_guidance, _handle_sos_error 已实现
- ✅ _show_sos_status_card 用st.container替代st.columns
- ✅ _provide_feedback 重构，避免嵌套columns
- ✅ _send_sos 异常处理改用 _handle_sos_error
- ✅ 所有UI术语已替换为用户友好版本
- ✅ 后台日志保持技术细节 (for debugging)
- ✅ 环境状态（signal, battery, retry）在错误路由中传递

---

## 下一步移植
此修复纯粹是 `views/monitoring.py` 中的改进，**不需要修改**:
- ✅ `app.streamlit.py` - render() 已正确集成
- ✅ `models/db.py` - 数据库架构无变化
- ✅ `services/satellite.py` - 卫星调度无变化
- ✅ `services/lstm_risk.py` - LSTM模型无变化

---

## 竞赛提交建议
- **技术评委**: 代码注释保留专业术语 ([SOS] prefix)，展示算法深度
- **用户体验评委**: UI显示纯中文、可操作、无技术jargon，展示人性化设计
- **现场演示**: 模拟弱信号/低电量场景，展示自动恢复机制

