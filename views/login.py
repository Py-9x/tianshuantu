import streamlit as st
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from models.db import add_user, get_user_by_username


def _safe_rerun():
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def render():
    st.markdown("""
        <style>
        /* ===== 0. 隐藏 Streamlit 默认元素 ===== */
        [data-testid="stHeader"], [data-testid="stToolbar"] { display: none !important; }
        .main .block-container { max-width: 100% !important; padding: 0 !important; margin: 0 !important; }

        /* ===== 1. 全局背景与字体 ===== */
        body, .stApp {
            background-color: #030a11 !important;
            color: #e2e8f0;
            overflow: hidden;
            font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        }

        /* ===== 2. 粒子画布 ===== */
        #particle-canvas {
            position: fixed;
            top: 0; left: 0;
            width: 100vw; height: 100vh;
            z-index: 1;
            pointer-events: none;
        }

        /* ===== 3. 关键帧动画 ===== */
        @keyframes gaze {
            0%, 100% { transform: scale(1.0) translate(0, 0); opacity: 0.9; }
            25%      { transform: scale(1.01) translate(-5px, 2px); opacity: 1.0; }
            75%      { transform: scale(0.99) translate(5px, -2px); opacity: 0.8; }
        }
        @keyframes scanline {
            0%   { top: -10%; }
            100% { top: 110%; }
        }
        @keyframes breathe {
            0%, 100% { text-shadow: 0 0 10px rgba(0,229,255,0.3), 0 2px 8px rgba(0,0,0,0.5); }
            50%      { text-shadow: 0 0 25px rgba(0,229,255,0.6), 0 2px 8px rgba(0,0,0,0.5); }
        }
        @keyframes borderGlow {
            0%, 100% { border-color: rgba(0, 198, 255, 0.3); box-shadow: 0 20px 60px rgba(0,0,0,0.7), 0 0 30px rgba(0,198,255,0.1); }
            50%      { border-color: rgba(0, 198, 255, 0.6); box-shadow: 0 20px 60px rgba(0,0,0,0.7), 0 0 45px rgba(0,198,255,0.25); }
        }
        @keyframes float {
            0%, 100% { transform: translateY(0px); }
            50%      { transform: translateY(-6px); }
        }
        @keyframes pulseRing {
            0%   { transform: translate(-50%, -50%) scale(0.95); opacity: 0.6; }
            50%  { transform: translate(-50%, -50%) scale(1.05); opacity: 1.0; }
            100% { transform: translate(-50%, -50%) scale(0.95); opacity: 0.6; }
        }

        /* ===== 4. 全景控制终端背景 ===== */
        .full-screen-console {
            position: fixed;
            top: 0; left: 0; width: 100vw; height: 100vh;
            display: flex; justify-content: space-between; align-items: center;
            background:
                radial-gradient(circle at center, rgba(0, 198, 255, 0.12), transparent 60%),
                linear-gradient(135deg, #05101a 0%, #030a11 100%);
            z-index: 0;
        }

        /* ===== 5. AI 监测核心视觉 ===== */
        .ai-core-gaze {
            position: absolute;
            top: 50%; left: 38%;
            width: 480px; height: 480px;
            transform: translate(-50%, -50%);
            border: 2px solid rgba(0, 198, 255, 0.15);
            border-radius: 50%;
            box-shadow: 0 0 100px rgba(0, 198, 255, 0.08), inset 0 0 50px rgba(0, 198, 255, 0.04);
            background:
                radial-gradient(circle at center, rgba(0, 198, 255, 0.35) 0%, transparent 18%),
                conic-gradient(from 180deg at 50% 50%, #00C6FF, transparent 15%, transparent 85%, #00C6FF),
                linear-gradient(90deg, transparent, rgba(0, 198, 255, 0.04), transparent);
            animation: gaze 10s ease-in-out infinite;
            z-index: 1;
        }
        /* 外层脉冲光环 */
        .ai-core-ring {
            position: absolute;
            top: 50%; left: 38%;
            width: 560px; height: 560px;
            transform: translate(-50%, -50%);
            border: 1px solid rgba(0, 198, 255, 0.08);
            border-radius: 50%;
            animation: pulseRing 6s ease-in-out infinite;
            z-index: 1;
        }

        /* ===== 6. 背景元数据文字 ===== */
        .console-metadata {
            position: absolute;
            font-family: 'Monaco', 'Menlo', 'Consolas', monospace;
            font-size: 10px;
            color: rgba(120, 170, 220, 0.7);
            letter-spacing: 1.5px;
            text-transform: uppercase;
        }

        /* ===== 7. 登录面板 — 高级玻璃态 ===== */
        [data-testid="column"]:nth-child(2) {
            background: linear-gradient(170deg, rgba(15, 22, 35, 0.92) 0%, rgba(8, 12, 22, 0.88) 100%) !important;
            border: 1px solid rgba(0, 198, 255, 0.35) !important;
            border-radius: 24px !important;
            padding: 45px 40px 40px 40px !important;
            backdrop-filter: blur(20px) saturate(1.5) !important;
            -webkit-backdrop-filter: blur(20px) saturate(1.5) !important;
            position: relative;
            overflow: hidden;
            animation: borderGlow 5s ease-in-out infinite !important;
        }

        /* 面板内扫描线效果 */
        [data-testid="column"]:nth-child(2)::before {
            content: '';
            position: absolute;
            top: -10%; left: 0;
            width: 100%; height: 3px;
            background: linear-gradient(90deg, transparent, rgba(0, 229, 255, 0.3), transparent);
            animation: scanline 4s linear infinite;
            z-index: 100;
            pointer-events: none;
        }

        /* 面板内环境光晕 */
        [data-testid="column"]:nth-child(2)::after {
            content: '';
            position: absolute;
            top: -30%; left: -20%; right: -20%;
            height: 200px;
            background: radial-gradient(ellipse at center, rgba(0, 198, 255, 0.06) 0%, transparent 70%);
            pointer-events: none;
            z-index: 0;
        }

        /* ===== 8. 顶部装饰状态条 ===== */
        .panel-status-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding: 6px 12px;
            background: rgba(0, 198, 255, 0.05);
            border: 1px solid rgba(0, 198, 255, 0.12);
            border-radius: 8px;
            font-family: 'Monaco', 'Menlo', 'Consolas', monospace;
            font-size: 10px;
            color: rgba(0, 229, 255, 0.7);
            letter-spacing: 1px;
        }
        .status-dot {
            display: inline-block;
            width: 6px; height: 6px;
            border-radius: 50%;
            background: #00E5FF;
            box-shadow: 0 0 6px rgba(0, 229, 255, 0.6);
            margin-right: 6px;
            animation: breathe 2s ease-in-out infinite;
        }

        /* ===== 9. 标题区 — 渐变 + 呼吸光效 ===== */
        .login-header {
            text-align: center;
            margin-bottom: 25px;
        }
        .logo-wrapper {
            margin: 0 auto 12px auto;
            font-size: 56px;
            text-shadow: 0 0 20px rgba(0, 198, 255, 0.25);
            animation: float 4s ease-in-out infinite;
        }
        .login-title {
            font-size: 36px;
            font-weight: 800;
            background: linear-gradient(135deg, #FFFFFF 0%, #00E5FF 50%, #FFFFFF 100%);
            background-size: 200% 200%;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            letter-spacing: 6px;
            margin-bottom: 8px;
            animation: breathe 3s ease-in-out infinite;
        }
        .terminal-status {
            font-size: 12px;
            color: #00E5FF !important;
            font-weight: 600;
            letter-spacing: 2px;
            text-shadow: 0 0 10px rgba(0, 229, 255, 0.4);
        }
        .divider-line {
            width: 60%;
            height: 1px;
            margin: 15px auto;
            background: linear-gradient(90deg, transparent, rgba(0, 198, 255, 0.4), transparent);
        }

        /* ===== 10. 输入框 — 精致聚焦 ===== */
        .stTextInput label { display: none !important; }

        .stTextInput > div > div > input {
            background-color: rgba(10, 15, 25, 0.85) !important;
            border: 1px solid rgba(0, 198, 255, 0.2) !important;
            border-radius: 12px !important;
            padding: 14px 18px 14px 18px !important;
            color: #FFFFFF !important;
            font-weight: 500 !important;
            font-family: 'Monaco', 'Menlo', 'Consolas', monospace !important;
            font-size: 14px !important;
            transition: all 0.35s cubic-bezier(0.4, 0, 0.2, 1) !important;
        }
        .stTextInput > div > div > input::placeholder {
            color: rgba(140, 180, 220, 0.6) !important;
            opacity: 1 !important;
        }
        .stTextInput > div > div > input:focus {
            background-color: rgba(12, 18, 30, 0.95) !important;
            border-color: #00E5FF !important;
            box-shadow: 0 0 20px rgba(0, 229, 255, 0.2), inset 0 0 8px rgba(0, 229, 255, 0.05) !important;
        }

        /* ===== 11. 登录按钮 — 渐变背景 ===== */
        .stButton > button,
        .stButton > button:focus,
        .stButton > button:active,
        .stButton > button:visited {
            width: 100% !important;
            height: 52px !important;
            margin-top: 18px !important;
            background: linear-gradient(135deg, #00B4DB 0%, #0083B0 50%, #00C6FF 100%) !important;
            background-size: 200% 200% !important;
            border: 1px solid rgba(0, 198, 255, 0.4) !important;
            border-radius: 14px !important;
            box-shadow: 0 8px 25px rgba(0, 130, 200, 0.3), inset 0 1px 0 rgba(255,255,255,0.1) !important;
            transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1) !important;
            cursor: pointer !important;
        }
        /* 按钮 hover */
        .stButton > button:hover {
            transform: translateY(-2px) !important;
            box-shadow: 0 12px 35px rgba(0, 180, 255, 0.4), 0 0 20px rgba(0, 229, 255, 0.2), inset 0 1px 0 rgba(255,255,255,0.15) !important;
            border-color: rgba(0, 229, 255, 0.7) !important;
        }
        /* 按钮 active 点击 */
        .stButton > button:active {
            transform: translateY(0px) !important;
            box-shadow: 0 4px 15px rgba(0, 130, 200, 0.3) !important;
        }

        /* ===== 12. 底部安全徽章 ===== */
        .security-badge {
            text-align: center;
            margin-top: 20px;
            padding: 8px;
            font-family: 'Monaco', 'Menlo', 'Consolas', monospace;
            font-size: 10px;
            color: rgba(100, 160, 220, 0.5);
            letter-spacing: 1.5px;
        }
        .security-badge .shield {
            display: inline-block;
            margin-right: 4px;
            font-size: 12px;
        }

        /* ===== 13. Alert 圆角 ===== */
        [data-testid="stAlert"] { border-radius: 12px !important; }
        </style>
    """, unsafe_allow_html=True)

    # ===== 渲染全屏控制终端背景 =====
    st.markdown("""
        <div class="full-screen-console">
            <div class="ai-core-ring"></div>
            <div class="ai-core-gaze"></div>
            <div class="console-metadata" style="top: 25px; left: 30px;">[SYSLOG] PERIMETER:SECURE | LIFE-SUPPORT:ACTIVE</div>
            <div class="console-metadata" style="bottom: 25px; left: 30px;">DEPLOYMENT CORE: 🛰️ TIANYA OPERATOR TERMINAL v5.0</div>
            <div class="console-metadata" style="top: 25px; right: 30px;">UTC-0 | MONITOR_LEVEL:7 | ENCRYPT:AES-256</div>
            <div class="console-metadata" style="bottom: 25px; right: 30px;">CONN:STABLE | LATENCY:12ms</div>
        </div>
        <canvas id="particle-canvas"></canvas>
    """, unsafe_allow_html=True)

    # ===== 粒子动画 JavaScript =====
    st.markdown("""
        <script>
        (function() {
            // 防止重复初始化
            if (window.__particlesInitialized) return;
            window.__particlesInitialized = true;

            const canvas = document.getElementById('particle-canvas');
            if (!canvas) return;
            const ctx = canvas.getContext('2d');

            let W, H;
            function resize() {
                W = canvas.width = window.innerWidth;
                H = canvas.height = window.innerHeight;
            }
            resize();
            window.addEventListener('resize', resize);

            // 鼠标位置
            let mouseX = W / 2, mouseY = H / 2;
            // canvas 设置了 pointer-events: none，所以我们监听 document
            document.addEventListener('mousemove', function(e) {
                mouseX = e.clientX;
                mouseY = e.clientY;
            });

            // 粒子数组
            const PARTICLE_COUNT = 130;
            const LINK_DIST = 120;
            const MOUSE_RADIUS = 200;
            const particles = [];

            function Particle() {
                this.x = Math.random() * W;
                this.y = Math.random() * H;
                this.vx = (Math.random() - 0.5) * 0.6;
                this.vy = (Math.random() - 0.5) * 0.6;
                this.radius = Math.random() * 1.8 + 0.5;
                this.opacity = Math.random() * 0.5 + 0.3;
            }

            for (let i = 0; i < PARTICLE_COUNT; i++) {
                particles.push(new Particle());
            }

            function animate() {
                ctx.clearRect(0, 0, W, H);

                for (let i = 0; i < particles.length; i++) {
                    const p = particles[i];

                    // 鼠标吸引力
                    const dx = mouseX - p.x;
                    const dy = mouseY - p.y;
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    if (dist < MOUSE_RADIUS) {
                        const force = (MOUSE_RADIUS - dist) / MOUSE_RADIUS * 0.015;
                        p.vx += dx * force;
                        p.vy += dy * force;
                    }

                    // 阻尼
                    p.vx *= 0.98;
                    p.vy *= 0.98;

                    p.x += p.vx;
                    p.y += p.vy;

                    // 边界回弹
                    if (p.x < 0 || p.x > W) p.vx *= -1;
                    if (p.y < 0 || p.y > H) p.vy *= -1;
                    p.x = Math.max(0, Math.min(W, p.x));
                    p.y = Math.max(0, Math.min(H, p.y));

                    // 画粒子
                    ctx.beginPath();
                    ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
                    ctx.fillStyle = 'rgba(0, 198, 255, ' + p.opacity + ')';
                    ctx.fill();

                    // 鼠标附近的粒子增加光晕
                    if (dist < MOUSE_RADIUS) {
                        ctx.beginPath();
                        ctx.arc(p.x, p.y, p.radius + 2, 0, Math.PI * 2);
                        ctx.fillStyle = 'rgba(0, 229, 255, ' + (0.15 * (1 - dist / MOUSE_RADIUS)) + ')';
                        ctx.fill();
                    }

                    // 粒子连线
                    for (let j = i + 1; j < particles.length; j++) {
                        const p2 = particles[j];
                        const lx = p.x - p2.x;
                        const ly = p.y - p2.y;
                        const ld = Math.sqrt(lx * lx + ly * ly);
                        if (ld < LINK_DIST) {
                            const alpha = (1 - ld / LINK_DIST) * 0.15;
                            ctx.beginPath();
                            ctx.moveTo(p.x, p.y);
                            ctx.lineTo(p2.x, p2.y);
                            ctx.strokeStyle = 'rgba(0, 198, 255, ' + alpha + ')';
                            ctx.lineWidth = 0.6;
                            ctx.stroke();
                        }
                    }
                }

                requestAnimationFrame(animate);
            }

            animate();
        })();
        </script>
    """, unsafe_allow_html=True)

    # ===== 按钮文字颜色强制修复 (注入<head>样式表) =====
    st.markdown("""
        <script>
        (function() {
            if (window.__btnStylerInitialized) return;
            window.__btnStylerInitialized = true;

            // 方法1：向 <head> 注入样式表（顺序靠后，优先级最高）
            function injectHeadStyle() {
                var existing = document.getElementById('__btn-color-override');
                if (existing) return;
                var s = document.createElement('style');
                s.id = '__btn-color-override';
                s.innerHTML = [
                    '.stButton > button { color: #ffffff !important; }',
                    '.stButton > button * { color: #ffffff !important; }',
                    '.stButton > button p { color: #ffffff !important; }',
                    '.stButton > button span { color: #ffffff !important; }',
                    'button[kind="secondary"] p { color: #ffffff !important; }',
                    'button[kind="secondary"] span { color: #ffffff !important; }',
                    'button[kind="secondary"] { color: #ffffff !important; }',
                    '[data-testid="stBaseButton-secondary"] { color: #ffffff !important; }',
                    '[data-testid="stBaseButton-secondary"] p { color: #ffffff !important; }',
                    '[data-testid="stBaseButton-secondary"] * { color: #ffffff !important; }',
                    'button[data-testid] p { color: #ffffff !important; }',
                    'button[data-testid] span { color: #ffffff !important; }'
                ].join('\\n');
                document.head.appendChild(s);
            }

            // 方法2：逐元素设置 inline style（最终保险）
            function forceInlineStyle() {
                var btns = document.querySelectorAll(
                    '.stButton > button, button[kind], [data-testid*="Button"]'
                );
                btns.forEach(function(btn) {
                    btn.style.setProperty('color', '#ffffff', 'important');
                    btn.querySelectorAll('*').forEach(function(c) {
                        c.style.setProperty('color', '#ffffff', 'important');
                    });
                });
            }

            // 立即执行两种方法
            injectHeadStyle();
            forceInlineStyle();

            // setInterval 轮询（每200ms，持续10秒）
            var n = 0;
            var t = setInterval(function() {
                injectHeadStyle();
                forceInlineStyle();
                if (++n >= 50) clearInterval(t);
            }, 200);

            // MutationObserver 持续保活
            new MutationObserver(function(muts) {
                if (muts.some(function(m) { return m.addedNodes.length > 0; })) {
                    requestAnimationFrame(forceInlineStyle);
                }
            }).observe(document.body, { childList: true, subtree: true });
        })();
        </script>
    """, unsafe_allow_html=True)

    # ===== 登录面板布局 =====
    col1, col2, col3 = st.columns([1, 1.3, 0.3])

    with col2:
        # 顶部装饰状态条
        st.markdown(
            '<div class="panel-status-bar">'
            '  <span><span class="status-dot"></span>SECURE CHANNEL</span>'
            '  <span>TLS 1.3 · AES-256</span>'
            '</div>',
            unsafe_allow_html=True
        )

        # 标题区
        st.markdown(
            "<div class='login-header'>"
            "    <div class='logo-wrapper'>🛰️</div>"
            "    <div class='login-title'>天枢安途</div>"
            "    <div class='divider-line'></div>"
            "    <div class='terminal-status'>"
            "        <span>高原探险全周期生命智能监护平台</span>"
            "    </div>"
            "</div>",
            unsafe_allow_html=True
        )

        st.write("")

        username = st.text_input("账号", placeholder="账号：请输入用户名")
        password = st.text_input("密码", type="password", placeholder="密码：请输入登录密码")

        if st.button("注册/登录系统"):
            if username.strip():
                uname = username.strip()
                if len(uname) < 4:
                    st.error("账号长度必须大于等于4个字符！")
                    return

                user = get_user_by_username(uname)
                if not user:
                    # Register the user if they don't exist
                    if len(password) < 8 or not any(char.isdigit() for char in password) or not any(char.isalpha() for char in password):
                        st.error("密码必须至少8个字符，包含字母和数字！")
                        return

                    pwd = password.strip()
                    add_user(uname, pwd, "")
                    st.success(f"注册成功，欢迎 {uname}！请继续登录。")
                    return
                else:
                    # Validate the password for existing users
                    if user['password'] != password:
                        st.error("密码错误，请重试！")
                        return

                # Successful login
                st.session_state['logged_in'] = True
                st.session_state['username'] = uname

                if user:
                    st.session_state['user_id'] = int(user["id"])
                    profile_done = bool(user["profile_complete"]) if "profile_complete" in user.keys() else bool(user["age"])
                    st.session_state['profile_complete'] = profile_done
                st.success(f"登录成功，欢迎 {uname}！正在进入系统...")
                _safe_rerun()
            else:
                st.error("请输入有效账号")

        # 底部安全徽章
        st.markdown(
            '<div class="security-badge">'
            '  <span class="shield">🔒</span> PROTECTED BY TIANYA DEFENSE GRID · v5.0'
            '</div>',
            unsafe_allow_html=True
        )
