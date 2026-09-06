#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模块2: publisher - 博客园自动发文
功能:
  1. 登录博客园（处理阿里云 checkbox 验证码）
  2. 填写标题+正文发布文章
  3. 返回文章URL和截图路径（给三丰云延期用）
"""

import re
import random
import logging
from pathlib import Path

logger = logging.getLogger("sanfengyun.publisher")


class CnblogsPublisher:
    """博客园发帖器"""

    LOGIN_URL = "https://account.cnblogs.com/signin"
    EDIT_URL = "https://i.cnblogs.com/posts/edit"

    def __init__(self, email: str, password: str, username: str = ""):
        self.email = email
        self.password = password
        self.username = username  # 博客用户名（URL里 xxx 来自 https://www.cnblogs.com/xxx）

    # --------------------------------------------------------------
    # 登录
    # --------------------------------------------------------------
    def login(self, page) -> bool:
        """登录博客园（含阿里云验证码处理）"""
        logger.info("[博客园] 登录中...")
        page.goto(self.LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # 已登录直接返回
        if "signin" not in page.url:
            logger.info("[博客园] 已登录")
            return True

        # 填账号密码
        try:
            page.locator('input[placeholder="登录用户名 / 邮箱"]').click()
            page.keyboard.type(self.email, delay=50)
            page.locator('input[placeholder="密码"]').click()
            page.keyboard.type(self.password, delay=50)
            page.locator("button.action-button:has-text('登录')").first.click()
            page.wait_for_timeout(3000)
        except Exception as e:
            logger.error(f"[博客园] 填表单失败: {e}")
            return False

        # 无验证码，直接跳转 = 成功
        if "signin" not in page.url:
            logger.info("[博客园] ✅ 登录成功（无验证码）")
            return True

        # 处理阿里云 checkbox
        logger.info("[博客园] 处理安全验证...")
        if self._handle_checkbox(page):
            page.wait_for_timeout(3000)
            if "signin" not in page.url:
                logger.info("[博客园] ✅ 登录成功")
                return True

        logger.error("[博客园] 登录失败")
        page.screenshot(path=str(Path(__file__).parent / "screenshots" / "cnblogs_login_fail.png"))
        return False

    def _handle_checkbox(self, page, max_retry=15) -> bool:
        """处理阿里云 checkbox 验证码（模拟人类鼠标轨迹）"""
        for attempt in range(max_retry):
            try:
                if "验证成功" in page.inner_text("body"):
                    return True
            except Exception:
                pass

            popup = page.locator("#aliyunCaptcha-window-popup").first
            if popup.count() == 0 or not popup.is_visible():
                page.wait_for_timeout(2000)
                continue

            box = page.locator("#aliyunCaptcha-checkbox-left").first.bounding_box()
            if not box:
                page.wait_for_timeout(2000)
                continue

            # 人性化点击：从远处曲线移动到 checkbox 图标
            tx = box["x"] + random.randint(15, 25)
            ty = box["y"] + box["height"] / 2 + random.uniform(-3, 3)
            sx = tx - random.randint(80, 150)
            sy = ty + random.randint(-40, 40)
            page.mouse.move(sx, sy)
            page.wait_for_timeout(random.randint(200, 500))
            # 多步曲线移动，模拟人类鼠标轨迹
            steps = random.randint(10, 20)
            for i in range(steps):
                t = (i + 1) / steps
                # 用贝塞尔曲线模拟自然移动
                ease = t * t * (3 - 2 * t)  # smoothstep
                x = sx + (tx - sx) * ease + random.uniform(-2, 2)
                y = sy + (ty - sy) * ease + random.uniform(-1, 1)
                page.mouse.move(x, y)
                page.wait_for_timeout(random.randint(15, 45))
            # 在目标附近停顿一下再点击
            page.wait_for_timeout(random.randint(100, 300))
            page.mouse.down()
            page.wait_for_timeout(random.randint(100, 200))
            page.mouse.up()
            page.wait_for_timeout(5000)

            if not popup.is_visible():
                return True
            # 验证失败，等待后重试
            page.wait_for_timeout(2000)
        return False

    # --------------------------------------------------------------
    # 发帖
    # --------------------------------------------------------------
    def publish(self, page, title: str, content: str,
                sanitize_from: str = "三丰云", sanitize_to: str = "三丰") -> tuple:
        """
        在博客园发布文章
        返回: (success: bool, article_url: str, screenshot_path: str)
        """
        # 替换敏感词
        if sanitize_from and sanitize_from != sanitize_to:
            title = title.replace(sanitize_from, sanitize_to)
            content = content.replace(sanitize_from, sanitize_to)

        logger.info("[博客园] 进入发文页...")
        page.goto(self.EDIT_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(6000)

        # 填标题
        page.locator("#post-title").click()
        page.keyboard.type(title, delay=30)
        logger.info(f"[博客园] 标题: {title}")

        # 填正文
        page.locator("#md-editor").click()
        page.keyboard.press("Control+A")
        page.keyboard.type(content, delay=5)
        logger.info(f"[博客园] 正文长度: {len(content)}")

        page.wait_for_timeout(1000)

        # 点击发布
        logger.info("[博客园] 点击发布...")
        page.locator("button.cnb-button:has-text('发布')").first.click()
        page.wait_for_timeout(5000)

        final_url = page.url
        logger.info(f"[博客园] 发布后 URL: {final_url}")

        # 判断是否成功
        is_published = "edit-done" in final_url or "isPublished=true" in final_url

        screenshot_dir = Path(__file__).parent / "screenshots"
        screenshot_dir.mkdir(exist_ok=True)

        if is_published:
            # 从 URL 提取 postId，构造正确文章链接
            m = re.search(r"postId[=;](\d+)", final_url)
            if m:
                post_id = m.group(1)
                username = self.username or self._detect_username(page)
                if username:
                    article_url = f"https://www.cnblogs.com/{username}/p/{post_id}"
                else:
                    article_url = f"https://www.cnblogs.com/p/{post_id}"
                logger.info(f"[博客园] ✅ 发布成功: {article_url}")
            else:
                article_url = final_url
            success = True
        else:
            # 检查错误（排除"成功"字样）
            try:
                errors = page.evaluate("""
                    () => {
                        const r = [];
                        document.querySelectorAll('[class*="error"], [class*="warning"], [class*="alert"], [class*="message"], [class*="toast"], [class*="ant-message"]').forEach(el => {
                            const t = (el.innerText || '').trim();
                            if (t && t.length < 200 && !t.includes('成功')) r.push(t);
                        });
                        return r;
                    }
                """)
                if errors:
                    logger.error(f"[博客园] ❌ 发布失败: {errors}")
            except Exception:
                pass
            page.screenshot(path=str(screenshot_dir / "cnblogs_publish_fail.png"))
            success = False
            article_url = ""

        # 保存文章页面截图（给三丰云延期用）
        # 🔒 关键：必须跳到文章**公开页**截图，不能在后台 edit-done 页截
        screenshot_path = str(screenshot_dir / "cnblogs_article.png")
        if success and article_url:
            logger.info(f"[博客园] 跳转到文章公开页截图: {article_url}")
            page.goto(article_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(3000)
            page.screenshot(path=screenshot_path, full_page=True)
            # 压缩到 JPG + 限制 500KB 内（三丰云审核偏好清晰+小）
            try:
                self._compress_screenshot(screenshot_path, max_kb=500)
            except Exception as e:
                logger.warning(f"[博客园] 截图压缩失败（继续用原图）: {e}")
            logger.info(f"[博客园] ✅ 公开页长截图: {screenshot_path}")
        else:
            page.screenshot(path=screenshot_path, full_page=True)

        return success, article_url, screenshot_path

    @staticmethod
    def _compress_screenshot(img_path: str, max_kb: int = 500):
        """PNG → JPG 压缩，文件超 max_kb 时降低质量直到达标"""
        import os
        try:
            from PIL import Image
        except ImportError:
            # 没装 Pillow 就跳过压缩
            return
        img_size = os.path.getsize(img_path)
        if img_size <= max_kb * 1024:
            return  # 已经够小
        img = Image.open(img_path).convert("RGB")  # JPG 不支持 alpha
        out_path = img_path.replace(".png", ".jpg")
        for quality in (85, 75, 65, 55):
            img.save(out_path, "JPEG", quality=quality, optimize=True)
            if os.path.getsize(out_path) <= max_kb * 1024:
                break
        else:
            # 还是超，缩宽到 1024
            w, h = img.size
            img = img.resize((1024, int(h * 1024 / w)), Image.LANCZOS)
            img.save(out_path, "JPEG", quality=60, optimize=True)
        # 替换原文件引用
        os.replace(out_path, img_path)
        logger.info(f"[博客园] 截图已压缩: {img_size//1024}KB → {os.path.getsize(img_path)//1024}KB")

    def _detect_username(self, page) -> str:
        """从页面推断博客用户名"""
        try:
            return page.evaluate("""
                () => {
                    const links = document.querySelectorAll('a[href*="cnblogs.com"]');
                    for (const a of links) {
                        const m = a.href.match(/cnblogs\\.com\\/([\\w\\-]+)/);
                        if (m && m[1].length > 3 && !['p','i','account','www'].includes(m[1])) {
                            return m[1];
                        }
                    }
                    return '';
                }
            """)
        except Exception:
            return ""

