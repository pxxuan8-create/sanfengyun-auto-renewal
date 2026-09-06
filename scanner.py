#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scanner.py - 扫描三丰云产品延期状态（DOM 页面元素抓取版）

执行顺序：登录检测 → 跳转产品详情页 → 提取 DOM 文本 → 判断延期状态

核心优势（对比 API 拦截）：
  - 页面文字为准，不依赖后端 JSON 字段，平台升级不会失效
  - 直接拿到"您还未到需要提交延期的时间"这类业务提示
  - 能识别蓝色"免费延期"按钮是否存在

登录检测逻辑（ensure_login 最先执行）：
  1. goto control 检查 session 是否已登录 + 手机号是否匹配
  2. 已登录且手机号匹配 → 直接返回 True（跳过登录）
  3. 未登录或手机号不匹配 → 执行退出 + 重新登录 + 修复 cookie domain
  4. fetch user.php cmd=user_info 验证 API 登录态
"""

import re
import json
import logging
from pathlib import Path

logger = logging.getLogger("sanfengyun.scanner")


class SanfengyunScanner:
    CONTROL_URL = "https://www.sanfengyun.com/control/#/product"
    LOGIN_URL = "https://www.sanfengyun.com/login"

    def __init__(self, phone: str, password: str):
        self.phone = phone
        self.password = password

    # ==============================================================
    # 【第一步】登录检测（必须最先执行）
    # ==============================================================
    def ensure_login(self, page) -> bool:
        """
        强制登录流程：清空旧 cookie → 访问登录页 → 填表单 → 修复 domain → 验证
        （三丰云 cookie domain 跨子域问题，不强制重登就会 API 返回未登录）
        """
        logger.info("[登录] 强制重新登录（清空旧 cookie）...")
        page.context.clear_cookies()
        return self._do_login(page)

    def _do_login(self, page) -> bool:
        """执行登录（带重试）"""
        logger.info(f"[登录] 登录账号: {self.phone}")

        # 加随机延迟模拟人类
        import time as _time
        _time.sleep(1)

        # 重试3次
        for attempt in range(3):
            try:
                logger.info(f"[登录] 第{attempt+1}次访问登录页...")
                page.goto(self.LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)
                break
            except Exception as e:
                logger.warning(f"[登录] 第{attempt+1}次访问失败: {e}")
                if attempt == 2:
                    logger.error("[登录] 3次都失败")
                    return False
                _time.sleep(3)

        try:
            page.locator("#userName").click()
            page.keyboard.type(self.phone, delay=50)
            page.locator("#passwordInput").click()
            page.keyboard.type(self.password, delay=50)
            page.locator("#loginSubmit").click()
            page.wait_for_timeout(5000)
        except Exception as e:
            logger.error(f"[登录] 填表单失败: {e}")
            return False

        self._fix_cookie_domain(page)
        return self._verify_api_login(page)

    def _fix_cookie_domain(self, page):
        """
        清空所有旧 cookie → 重新添加 domain=.sanfengyun.com 的 cookie
        让 api.sanfengyun.com 也能拿到登录态
        """
        try:
            old_cookies = page.context.cookies()
            # 只保留 sanfengyun 相关的，其他不动
            sf_cookies = [c for c in old_cookies if "sanfengyun" in c.get("domain", "")]
            if not sf_cookies:
                logger.info("[登录] 没有 sanfengyun 相关 cookie，跳过")
                return

            # 清空 sanfengyun 的旧 cookie
            page.context.clear_cookies()
            # 重建：domain 全部改成 .sanfengyun.com，SameSite=Lax，Secure=false
            new_cookies = []
            for c in sf_cookies:
                new_c = {
                    "name": c["name"],
                    "value": c["value"],
                    "domain": ".sanfengyun.com",
                    "path": c.get("path", "/"),
                    "httpOnly": c.get("httpOnly", False),
                    "secure": False,  # 关键：不要 secure，否则 http 也拿不到
                    "sameSite": "Lax",
                }
                if "expires" in c and c["expires"] and c["expires"] != -1:
                    new_c["expires"] = int(c["expires"])
                new_cookies.append(new_c)

            page.context.add_cookies(new_cookies)
            logger.info(f"[登录] ✅ 重建 {len(new_cookies)} 个 cookie (domain=.sanfengyun.com)")

            # 验证：看看 cookie 列表
            cookies_after = page.context.cookies()
            for c in cookies_after:
                if "sanfengyun" in c.get("domain", ""):
                    logger.info(f"[登录]   cookie: {c['name'][:20]} domain={c['domain']} samesite={c.get('sameSite','?')} secure={c.get('secure','?')}")
        except Exception as e:
            logger.warning(f"[登录] 修复 cookie 异常: {e}")

    def _verify_api_login(self, page) -> bool:
        """
        验证登录态（以 control 页面 DOM 检查为主）
        API fetch 经常被 SPA 导航打断，降级为可选调试
        """
        # 等页面稳定（登录后 SPA 可能自动跳转 control）
        page.wait_for_timeout(3000)

        # 主方案：跳 control 页面，检查 body 文本里是否有"未登录"
        try:
            page.goto(self.CONTROL_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            body = page.inner_text("body")
            if "尚未登录" in body or "未登录" in body:
                logger.error("[登录] ❌ control 页面显示未登录")
                return False
            logger.info("[登录] ✅ control 页面确认已登录")

            # 附加：尝试用 fetch 拿到手机号（调试用，失败不影响登录判断）
            try:
                resp = page.evaluate("""
                    async () => {
                        try {
                            const r = await fetch('https://api.sanfengyun.com/www/user.php', {
                                method: 'POST',
                                credentials: 'include',
                                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                                body: 'cmd=user_info'
                            });
                            return await r.text();
                        } catch(e) { return ''; }
                    }
                """)
                if resp.startswith("{"):
                    obj = json.loads(resp)
                    msg = obj.get("msg", {}) if isinstance(obj, dict) else {}
                    mobile = msg.get("ID_Mobile", "") if isinstance(msg, dict) else ""
                    if mobile:
                        logger.info(f"[登录] API 手机号: {mobile}")
                    else:
                        logger.info("[登录] API 正常（无手机号字段）")
            except Exception:
                pass  # API 调试失败不影响

            return True
        except Exception as e:
            logger.warning(f"[登录] control 页面检查异常: {e}")
            # 兜底：看 cookie 里有没有 session_id
            try:
                cookies = page.context.cookies()
                has_session = any(
                    c["name"] == "session_id" and "sanfengyun" in c.get("domain", "")
                    for c in cookies
                )
                if has_session:
                    logger.info("[登录] ⚠️ 页面检查异常但 cookie 有 session_id，当作已登录")
                    return True
            except Exception:
                pass
            logger.error("[登录] ❌ 所有验证失败")
            return False

    # ==============================================================
    # 【第二步】扫描（DOM 页面元素抓取，权威来源）
    # ==============================================================
    def scan_product(self, page, product: dict) -> dict:
        """
        跳转产品详情页 → 等 Vue 渲染 → 提取 DOM 文本
        判断规则（页面文字为准，不用本地时间算）：
          - 页面含"您还未到需要提交延期的时间" → can_renew=False
          - 页面有蓝色"免费延期"按钮可点击 → can_renew=True
          - 其他情况 → 综合判断
        """
        name = product["name"]
        ptype = product["ptype"]
        page_url = product.get("url", "")
        logger.info(f"[扫描] >>> {name} ({ptype})")

        # 1) 跳转 + 等渲染（Vue SPA，要等 networkidle + 额外 5 秒，让 header 的到期时间也渲染出来）
        logger.info(f"[扫描]   跳转: {page_url}")
        page.goto(page_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(5000)  # SPA 异步渲染，多等确保 header 区域（到期时间）也出来

        # 2) 先在默认 tab（基本信息）抓到期时间 —— header 区域所有 tab 都有，多等后肯定能抓到
        default_text = page.inner_text("body")

        # 从 URL 解析实例 ID 和类型（提前到这里，未开通分支也要用）
        instance_id = ""
        instance_type = ""
        id_match = re.search(r"/(freeServer|freeVhost)/(\d+)", page_url)
        if id_match:
            instance_type = id_match.group(1)
            instance_id = id_match.group(2)
            logger.info(f"[扫描]   实例: {instance_type}/{instance_id}")

        # 2.5) 检测产品是否未开通 —— 未开通直接返回，不继续扫描
        not_activated_markers = [
            "未开通", "尚未开通", "暂未开通", "未购买", "未创建",
            "没有开通", "请先开通", "去开通", "立即开通", "暂无产品",
            "您还没有", "未找到该", "产品不存在", "已过期未续费",
        ]
        for marker in not_activated_markers:
            if marker in default_text:
                logger.warning(f"[扫描]   ⚫ 检测到 \"{marker}\" → 产品未开通，跳过")
                # 截图方便排查
                try:
                    page.screenshot(path=str(Path(__file__).parent / "screenshots" / f"scan_{ptype}_not_activated.png"))
                except Exception:
                    pass
                return {
                    "name": name, "ptype": ptype, "page_url": page_url,
                    "instance_id": instance_id, "instance_type": instance_type,
                    "expire_time": "", "can_renew": False, "renew_time": "",
                    "form_status": "not_activated",
                }

        expire_time = self._extract_expire_time(default_text, logger)

        # 如果还是没抓到，再等 3 秒重试一次（防止 SPA 渲染延迟）
        if not expire_time:
            logger.info("[扫描]   默认 tab 没抓到到期时间，再等 3 秒重试...")
            page.wait_for_timeout(3000)
            default_text = page.inner_text("body")
            expire_time = self._extract_expire_time(default_text, logger)

        # 3) 点击"免费延期" tab —— 关键提示（如"还未到时间"）只在这个 tab 里
        tab_clicked = self._click_renew_tab(page)
        logger.info(f"[扫描]   免费延期 tab: {'✅ 已点击' if tab_clicked else '⚠️ 未找到或已是当前tab'}")
        page.wait_for_timeout(2500)

        # 5) 拿全文 DOM 文本（已切到延期 tab）
        try:
            page_text = page.inner_text("body")
        except Exception as e:
            logger.error(f"[扫描]   读取页面文本失败: {e}")
            page.screenshot(path=str(Path(__file__).parent / "screenshots" / "scan_error.png"))
            return {
                "name": name, "ptype": ptype, "page_url": page_url,
                "instance_id": instance_id, "instance_type": instance_type,
                "expire_time": expire_time, "can_renew": False, "renew_time": "",
                "error": str(e),
            }

        # 如果默认 tab 没抓到到期时间，在延期 tab 再试一次
        if not expire_time:
            expire_time = self._extract_expire_time(page_text, logger)

        # 6) 判断是否未到延期时间（虚拟主机特有提示）
        can_renew = True
        not_yet_markers = [
            "您还未到需要提交延期的时间",
            "还未到需要提交延期",
            "未到提交延期",
            "还未到延期时间",
        ]
        for marker in not_yet_markers:
            if marker in page_text:
                can_renew = False
                logger.info(f"[扫描]   🟢 未到延期时间（页面提示: {marker}）")
                break

        # 7) 提取下次可提交时间（从"请在 2026-09-20 07:58:22 后提交"文本）
        renew_time = ""
        renew_match = re.search(
            r"请在(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{1,2}:\d{1,2})后",
            page_text
        )
        if renew_match:
            renew_time = renew_match.group(1).replace("/", "-")
            logger.info(f"[扫描]   ✅ 可提交时间: {renew_time}")

        # 8) 检查【免费延期】按钮状态（云服务器页面，有这个按钮 = 可以延期）
        if can_renew:
            try:
                delay_btn = page.locator("button:has-text('免费延期')").first
                if delay_btn.count() > 0 and delay_btn.is_visible():
                    logger.info("[扫描]   🔴 免费延期按钮存在 → 允许提交")
                else:
                    logger.info("[扫描]   ⚠️ 未找到免费延期按钮，综合判断")
            except Exception as e:
                logger.warning(f"[扫描]   检查按钮异常: {e}")

        # 9) 判断延期 tab 里的表单状态（关键：决定要不要去发帖）
        #    ready      → 有发帖地址输入框，表单空，可以提交
        #    in_review  → 有"审核中"相关文字，之前已提交，等审核结果
        #    not_yet    → 未到时间
        form_status = "not_yet"
        if can_renew:
            # 检查有没有审核中相关提示
            review_markers = ["审核中", "处理中", "审核", "待审核", "提交成功"]
            review_hit = next((m for m in review_markers if m in page_text), None)
            # 检查有没有"发帖地址"输入框（可提交的标志）
            has_form_input = False
            try:
                form_input = page.locator("input[placeholder*='发帖'], input[placeholder*='网址'], textarea").first
                has_form_input = form_input.count() > 0 and form_input.is_visible()
            except Exception:
                has_form_input = "发帖地址" in page_text and ("请输入" in page_text or "http" in page_text.lower())

            if review_hit:
                form_status = "in_review"
                logger.info(f"[扫描]   🟡 延期 tab 检测到 \"{review_hit}\" → 审核中")
            elif has_form_input:
                form_status = "ready"
                logger.info("[扫描]   🟢 延期 tab 有发帖表单 → 可提交")
            else:
                # 有免费延期按钮（外部）但 tab 内没表单，综合判断
                form_status = "ready"
                logger.info("[扫描]   🟢 默认可提交（有免费延期按钮 + 无审核提示）")
        logger.info(f"[扫描]   表单状态: {form_status}")

        # 10) 云服务器特殊：几乎随时可以延期，只要没"未到时间"提示就认为可以
        if instance_type == "freeServer" and can_renew is not False:
            can_renew = True

        # 11) 扫描结果汇总
        icon = {"ready": "🟢", "in_review": "🟡", "not_yet": "🟢", "not_activated": "⚫"}.get(form_status, "🔴")
        status = {"ready": "可提交延期", "in_review": "审核中", "not_yet": "未到提交时间", "not_activated": "未开通"}.get(form_status, "?")
        logger.info(f"[扫描] {icon} {name}: {status}  到期={expire_time or '?'}  可提交={renew_time or '现在'}")

        # 截图（方便验证）
        try:
            page.screenshot(path=str(Path(__file__).parent / "screenshots" / f"scan_{ptype}.png"))
        except Exception:
            pass

        return {
            "name": name, "ptype": ptype, "page_url": page_url,
            "instance_id": instance_id, "instance_type": instance_type,
            "expire_time": expire_time, "can_renew": can_renew,
            "renew_time": renew_time, "form_status": form_status,
        }

    def scan_all(self, page, products: list) -> list:
        return [self.scan_product(page, p) for p in products if p.get("enabled", True)]

    @staticmethod
    def _extract_expire_time(text: str, logger) -> str:
        """从页面文本提取到期时间（先严格匹配，再近邻匹配）"""
        m = re.search(r"到期时间[:：]\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{1,2}:\d{1,2})", text)
        if m:
            result = m.group(1).replace("/", "-")
            logger.info(f"[扫描]   ✅ 到期时间: {result}")
            return result
        m2 = re.search(r"到期[^\n]{0,30}?(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{1,2}:\d{1,2})", text)
        if m2:
            result = m2.group(1).replace("/", "-")
            logger.info(f"[扫描]   ✅ 到期时间(近邻匹配): {result}")
            return result
        logger.warning("[扫描]   ⚠️ 未找到到期时间文本")
        return ""

    def _click_renew_tab(self, page) -> bool:
        try:
            result = page.evaluate("""
                () => {
                    const items = document.querySelectorAll('.el-tabs__item, [role="tab"], button, span, div');
                    for (const el of items) {
                        const t = (el.innerText || '').trim();
                        if (t === '免费延期' || t === '延期') {
                            const r = el.getBoundingClientRect();
                            if (r.width > 0 && r.width < 200 && r.height > 0) {
                                el.click(); return true;
                            }
                        }
                    }
                    return false;
                }
            """)
            return bool(result)
        except Exception:
            return False

