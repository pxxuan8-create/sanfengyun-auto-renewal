#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块3: renewer - 三丰云填写延期表单并提交
从截图看 vps 详情页的延期表单结构:
  发帖地址: [input placeholder="请输入发帖网址http://"]
  发帖截图: [input type="file" id="fileImg1" name="CertImg"]  [选择文件]
  [提交]  [确定]
"""

import re
import logging
from pathlib import Path

logger = logging.getLogger("sanfengyun.renewer")

SCREENSHOT_DIR = Path(__file__).parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)


class SanfengyunRenewer:
    """三丰云延期填表器"""

    def fill_and_submit(self, page, ptype: str, article_url: str,
                        screenshot_path: str, dry_run: bool = False,
                        page_url: str = "") -> dict:
        """
        进入产品详情页 → 滚动到发帖表单 → 填URL → 上传截图 → (可选)提交
        """
        action = "（仅填写，不提交）" if dry_run else ""
        logger.info(f"[延期] {ptype} {action}")

        # 1) 跳转到产品详情页
        if page_url:
            page.goto(page_url, wait_until="domcontentloaded", timeout=30000)

        # 2) 等待 + 点击"免费延期" tab
        for i in range(30):
            page.wait_for_timeout(1000)
            body = page.inner_text("body")
            if "发帖地址" in body and "发帖截图" in body:
                logger.info(f"[延期] 发帖表单已加载（第{i+1}秒）")
                break
            if "还未到" in body or "未到需要" in body:
                logger.info(f"[延期] 未到延期时间（第{i+1}秒），有提示")
                # 点免费延期 tab 确认一下（tab 里才会显示完整提示）
                self._click_renew_tab(page)
                page.wait_for_timeout(2000)
                body = page.inner_text("body")
                if "还未到" in body or "未到需要" in body:
                    logger.warning("[延期] 🟢 确认未到延期时间，跳过")
                    return {"success": False, "response_text": "未到延期时间", "next_renew_time": ""}
                break
            # 点击 免费延期 tab
            self._click_renew_tab(page)
            page.wait_for_timeout(1500)
            if i % 5 == 0 and i > 0:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight); window.scrollTo(0, 0);")

        # 3) 滚动到表单 + 截图
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1000)
        page.screenshot(path=str(SCREENSHOT_DIR / f"sf_{ptype}_form.png"))

        # 4) 填发帖 URL
        self._fill_url(page, article_url)

        # 5) 上传截图
        self._upload_screenshot(page, screenshot_path)

        # 截图已填好的表单
        page.screenshot(path=str(SCREENSHOT_DIR / f"sf_{ptype}_filled.png"), full_page=True)
        logger.info("[延期] ✅ 表单已填写")

        if dry_run:
            logger.info("[延期] 测试模式，跳过提交")
            return {"success": True, "response_text": "dry_run", "next_renew_time": ""}

        # 6) 点击提交
        return self._submit(page, ptype)

    # ---- 内部 ----

    def _click_renew_tab(self, page):
        """点击 免费延期 tab"""
        try:
            result = page.evaluate("""
                () => {
                    const items = document.querySelectorAll('.el-tabs__item, [role="tab"], button, span, div');
                    for (const el of items) {
                        const t = (el.innerText || '').trim();
                        if (t === '免费延期' || t === '延期') {
                            const r = el.getBoundingClientRect();
                            if (r.width > 0 && r.width < 200 && r.height > 0) {
                                el.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }
            """)
            return bool(result)
        except Exception:
            return False

    def _fill_url(self, page, article_url: str):
        """填发帖地址。从截图看 placeholder="请输入发帖网址http://" """
        # 优先用 placeholder 精确匹配
        selectors = [
            'input[placeholder*="发帖网址"]',
            'input[placeholder*="发帖地址"]',
            'input[placeholder*="发帖"]',
            'input[placeholder*="网址"]',
        ]
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.count() > 0 and el.is_visible():
                    el.click()
                    page.keyboard.press("Control+A")
                    page.keyboard.type(article_url, delay=10)
                    logger.info(f"[延期] ✅ 已填 URL: {article_url}")
                    return
            except Exception:
                continue

        # 兜底: 找 "发帖地址" label 右边的 input
        try:
            url_input = page.evaluate("""
                () => {
                    const labels = document.querySelectorAll('label, span, div');
                    for (const lb of labels) {
                        if ((lb.innerText || '').trim() === '发帖地址') {
                            const row = lb.closest('.el-form-item, .el-row, .form-item, div') || lb.parentElement;
                            const input = row ? row.querySelector('input[type="text"]') : null;
                            return input ? input : null;
                        }
                    }
                    return null;
                }
            """)
            if url_input:
                page.evaluate("(el, url) => { el.value = url; el.dispatchEvent(new Event('input')); }", url_input, article_url)
                logger.info(f"[延期] ✅ 已填 URL (JS方式): {article_url}")
                return
        except Exception as e:
            logger.error(f"[延期] 填 URL 失败: {e}")

    def _upload_screenshot(self, page, screenshot_path: str):
        """上传发帖截图。从截图看 input[type=file] id="fileImg1" """
        try:
            # 优先精确匹配
            file_input = page.locator('input#fileImg1, input[type="file"]').first
            if file_input.count() > 0:
                file_input.set_input_files(screenshot_path)
                logger.info(f"[延期] ✅ 已上传截图")
                page.wait_for_timeout(3000)
                return
        except Exception as e:
            logger.error(f"[延期] 上传截图失败: {e}")

    def _submit(self, page, ptype: str) -> dict:
        """点击提交按钮，读取结果"""
        logger.info("[延期] 点击提交...")
        # 先检查页面是否已处于"审核中/等待审核"状态（说明之前已提交成功）：
        # 此时没有可点的提交按钮，直接视为已提交，避免误报"没找到提交按钮"失败
        try:
            body_now = page.inner_text("body")
            if any(k in body_now for k in ["等待审核", "待审核中", "审核中", "正在审核", "提交成功", "处理中", "稍后查看"]):
                logger.info("[延期] 页面已处于审核中（之前已提交成功），无需重复提交")
                return {"success": True, "response_text": "已提交（审核中）", "next_renew_time": ""}
        except Exception:
            pass
        try:
            submit = page.locator("button:has-text('提交')").first
            if submit.count() == 0 or not submit.is_visible():
                logger.error("[延期] 没找到提交按钮")
                return {"success": False, "response_text": "没找到提交按钮", "next_renew_time": ""}

            submit.click()
            page.wait_for_timeout(5000)

            resp = page.inner_text("body")
            page.screenshot(path=str(SCREENSHOT_DIR / f"sf_{ptype}_submitted.png"))

            success = any(k in resp for k in ["提交成功", "已提交", "审核中", "等待审核"])
            failed = any(k in resp for k in ["失败", "未通过", "错误", "Error", "请先"])

            # 解析下次可延期时间
            next_time = ""
            m = re.search(r"请在(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{1,2}:\d{1,2})后", resp)
            if m:
                next_time = m.group(1).replace("/", "-")

            if success:
                logger.info("[延期] ✅ 提交成功")
            elif failed:
                logger.error(f"[延期] ❌ 提交失败")
            else:
                logger.info(f"[延期] 结果待确认")

            return {"success": success or not failed, "response_text": resp[:800], "next_renew_time": next_time}

        except Exception as e:
            logger.error(f"[延期] 提交异常: {e}")
            return {"success": False, "response_text": str(e), "next_renew_time": ""}

