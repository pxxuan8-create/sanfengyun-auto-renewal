#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三丰云免费产品自动延期脚本 v3
由三个模块组成:
  scanner   - 扫描产品到期时间 + 智能登录检测
  publisher - 博客园自动发文
  renewer   - 三丰云填表 + 提交延期

用法:
  python3 sanfengyun_renewal.py --test     # 测试模式：扫描→发文→填表(不提交)
  python3 sanfengyun_renewal.py --once     # 单次执行：扫描→到期就完整提交
  python3 sanfengyun_renewal.py            # 持续运行：智能倒计时自动循环
"""

import sys
import re
import json
import time
import logging
import argparse
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(BASE_DIR))

import yaml
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from scanner import SanfengyunScanner
from publisher import CnblogsPublisher
from renewer import SanfengyunRenewer
from article_generator import ArticleGenerator
from email_notify import EmailNotifier


# ==========================================================================
# 基础设置
# ==========================================================================

SCREENSHOT_DIR = BASE_DIR / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

# 持久化文件：保存每个实例的下次允许提交时间戳
STATE_FILE = BASE_DIR / "instance_timestamps.json"
SLEEP_SLICE = 60        # 分片 sleep 60s，支持 Ctrl+C 优雅退出
TRIGGER_DELAY = 60      # 到下次提交时间后，再等 60s 才触发扫描（缓冲服务器时间差）
URGENT_HINT_THRESHOLD = 1800  # 距离倒计时 <=30分钟 时推送"即将触发"提醒


def setup_logging(cfg: dict):
    level = cfg.get("level", "INFO").upper()
    handlers = [logging.StreamHandler(sys.stdout)]
    log_file = cfg.get("file", "")
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


logger = logging.getLogger("sanfengyun")


# ==========================================================================
# 持久化读写：每个实例独立保存 next_allow_submit_ts
# ==========================================================================

def load_instance_state() -> dict:
    """读取 instance_timestamps.json；文件不存在 / 损坏返回 {}。
    结构：{"freeServer_532542": {"next_allow_submit_ts": 1788619201}, ...}"""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_instance_state(state: dict):
    """持久化写回 json。进程重启后从这个文件恢复每个实例的倒计时，不用重新扫网页。"""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存状态文件失败: {e}")


def _instance_key(product: dict) -> str:
    """从 product dict 生成持久化 key: freeServer_532542"""
    url = product.get("url", "")
    m = re.search(r"/(freeServer|freeVhost)/(\d+)", url)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    # 兜底：用 ptype + key 字段
    return f"{product.get('ptype','unknown')}_{product.get('key','unknown')}"


def load_config() -> dict:
    """加载 config.yaml，并允许用环境变量覆盖敏感字段（GitHub Secrets 注入）。"""
    cfg_path = BASE_DIR / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    # 环境变量覆盖（GitHub Actions Secrets 优先，其次用配置文件里的值）
    env_overrides = {
        ("sanfengyun", "phone"): "SANFENGYUN_PHONE",
        ("sanfengyun", "password"): "SANFENGYUN_PASSWORD",
        ("cnblogs", "email"): "CNBLOGS_EMAIL",
        ("cnblogs", "password"): "CNBLOGS_PASSWORD",
        ("cnblogs", "username"): "CNBLOGS_USERNAME",
    }
    for (section, key), env_name in env_overrides.items():
        val = os.environ.get(env_name, "").strip()
        if val:
            cfg.setdefault(section, {})[key] = val

    # 产品 URL 覆盖（可选，避免把实例 ID 写进公开仓库）
    url_overrides = {
        "vps": "SANFENGYUN_VPS_URL",
        "vhost": "SANFENGYUN_VHOST_URL",
    }
    for product in cfg.get("products", []):
        env_name = url_overrides.get(product.get("key", ""))
        if env_name:
            val = os.environ.get(env_name, "").strip()
            if val:
                product["url"] = val

    setup_logging(cfg.get("logging", {}))
    return cfg


def find_chromium() -> str:
    """自动查找 chromium 可执行文件"""
    import glob
    home = str(Path.home())
    patterns = [
        f"{home}/.cache/ms-playwright/chromium-*/chrome-linux64/chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ]
    for pat in patterns:
        matches = glob.glob(pat)
        if matches:
            return matches[0]
    return ""


def launch_browser(pw, headless=True):
    """启动 chromium 浏览器（headless=False 需配合 Xvfb 虚拟显示）"""
    chromium = find_chromium()
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
    ]
    if chromium:
        logger.info(f"[浏览器] 使用 chromium: {chromium} (headless={headless})")
        return pw.chromium.launch(headless=headless, executable_path=chromium, args=args)
    logger.warning("[浏览器] 未找到 chromium，使用默认")
    return pw.chromium.launch(headless=headless, args=args)


def new_context(browser):
    """创建 context，注入反 webdriver 检测脚本"""
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    # 隐藏 webdriver 特征，绕过阿里云验证码检测
    ctx.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
    """)
    return ctx


def new_page(context):
    """创建新 page 并注入 stealth"""
    page = context.new_page()
    Stealth().apply_stealth_sync(page)
    return page


def _ensure_display():
    """确保有 DISPLAY 环境变量（Xvfb 虚拟显示），用于 headless=False 模式"""
    import os
    import subprocess
    if not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":99"
        # 检查 Xvfb 是否已在 :99 上运行
        try:
            result = subprocess.run(["pgrep", "-f", "Xvfb :99"], capture_output=True, timeout=2)
            if result.returncode != 0:
                # Xvfb 未运行，后台启动
                subprocess.Popen(
                    ["Xvfb", ":99", "-screen", "0", "1280x900x24"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                logger.info("[浏览器] 已启动 Xvfb 虚拟显示 :99")
        except FileNotFoundError:
            logger.warning("[浏览器] Xvfb 未安装，headless=False 可能失败（apt install xvfb）")
        except Exception as e:
            logger.warning(f"[浏览器] 启动 Xvfb 失败: {e}")


# ==========================================================================
# 主流程
# ==========================================================================

def run_test(cfg: dict, notifier):
    """
    测试模式: 扫描所有产品 → 可延期的执行发文+填表(不提交)
    """
    logger.info("=" * 60)
    logger.info("测试模式: 扫描 → 发文 → 填表（不提交）")
    logger.info("=" * 60)

    sf_cfg = cfg["sanfengyun"]
    cb_cfg = cfg["cnblogs"]
    settings = cfg.get("settings", {})
    products = [p for p in cfg.get("products", []) if p.get("enabled", True)]
    # 按 scan_scope 过滤：all=全部, vps=只云服务器, vhost=只虚拟主机
    _scope = settings.get("scan_scope", "all")
    if _scope in ("vps", "vhost"):
        products = [p for p in products if p.get("ptype") == _scope]
        logger.info(f"[配置] scan_scope={_scope}，只扫描: {[p['name'] for p in products]}")

    scanner = SanfengyunScanner(sf_cfg["phone"], sf_cfg["password"])
    publisher = CnblogsPublisher(cb_cfg["email"], cb_cfg["password"], cb_cfg.get("username", ""))
    renewer = SanfengyunRenewer()
    gen = ArticleGenerator()

    # 敏感词替换
    kw = settings.get("sanitize_keywords", ["三丰云", "三丰"])
    sanitize_from = kw[0] if kw else "三丰云"
    sanitize_to = kw[1] if len(kw) > 1 else "三丰"

    _ensure_display()
    with sync_playwright() as pw:
        browser = launch_browser(pw, headless=False)
        ctx = new_context(browser)

        try:
            # ========== 三丰云统一用一个 sf_page（sessionStorage 不共享给新 page）==========
            sf_page = new_page(ctx)
            if not scanner.ensure_login(sf_page):
                logger.error("三丰云登录失败，退出")
                return

            # ------- 阶段1: 扫描（用同一个 sf_page 跳不同 URL）-------
            logger.info("\n" + "=" * 60)
            logger.info("阶段1: 扫描所有产品延期状态")
            logger.info("=" * 60)

            scan_results = []
            for product in products:
                try:
                    result = scanner.scan_product(sf_page, product)
                    scan_results.append(result)
                except Exception as e:
                    logger.exception(f"  扫描 {product['name']} 异常: {e}")

            # 打印扫描汇总
            logger.info("\n--- 扫描汇总 ---")
            for r in scan_results:
                icon = "🔴" if r["can_renew"] else "🟢"
                status = "可提交延期" if r["can_renew"] else "未到提交时间"
                expire = r["expire_time"] or "未知"
                renew = r["renew_time"] or "现在"
                logger.info(f"  {icon} {r['name']}: {status}  到期={expire}  可提交={renew}")

            if scan_results:
                _notify_scan_result(notifier, scan_results)

            # ------- 阶段2: 博客园（独立 page，只登录一次，连续发文）-------
            # 只对 form_status=ready 的产品发文；in_review/not_yet/not_activated 都跳过
            skipped = [p for p in scan_results if p["can_renew"] and p.get("form_status") != "ready"]
            for p in skipped:
                logger.info(f"  ⏭️ 跳过 {p['name']}（状态={p.get('form_status')}，不发文）")
            can_renew_products = [p for p in scan_results if p["can_renew"] and p.get("form_status") == "ready"]

            if can_renew_products:
                logger.info(f"\n--- 阶段2: 博客园发文（{len(can_renew_products)} 篇，只登录一次）---")
                pub_page = new_page(ctx)
                try:
                    if not publisher.login(pub_page):
                        logger.error("博客园登录失败")
                        for p in can_renew_products:
                            notifier.notify_submit_failed(p["name"], "博客园登录失败", product=p)
                        can_renew_products = []
                    else:
                        for product in can_renew_products:
                            title, content = gen.generate(product_name=product["name"])
                            pub_ok, article_url, screenshot_path = publisher.publish(
                                pub_page, title, content,
                                sanitize_from=sanitize_from, sanitize_to=sanitize_to,
                            )
                            if not pub_ok or not article_url:
                                product["article_url"] = ""
                                product["screenshot_path"] = ""
                                notifier.notify_submit_failed(product["name"], "博客园发布失败", product=product)
                                continue
                            logger.info(f"  ✅ {product['name']}: {article_url}")
                            product["article_url"] = article_url
                            product["screenshot_path"] = screenshot_path
                            notifier.notify_article_posted(product["name"], article_url, title)
                except Exception as e:
                    logger.exception(f"博客园异常: {e}")
                finally:
                    pub_page.close()

            # ------- 阶段3: 三丰云填表（继续用 sf_page，跳不同 URL）-------
            for product in can_renew_products:
                if not product.get("article_url"):
                    continue

                logger.info(f"\n--- 阶段3: 三丰云填表 {product['name']} ---")
                try:
                    # sf_page 已登录，直接 fill_and_submit（它内部会跳到 product['page_url']）
                    result = renewer.fill_and_submit(
                        sf_page, product["ptype"],
                        product["article_url"], product["screenshot_path"],
                        dry_run=True,
                        page_url=product.get("page_url") or product.get("url", ""),
                    )
                    logger.info(f"  ✅ {product['name']} 表单已填写（未提交）")
                    notifier.send_text(
                        f"【测试模式】{product['name']}\n"
                        f"博客园发文 + 三丰云填表（未提交）\n"
                        f"URL: {product['article_url']}"
                    )
                except Exception as e:
                    logger.exception(f"  填表异常: {e}")

            sf_page.close()

            logger.info("\n" + "=" * 60)
            logger.info("✅ 测试模式完成")
            logger.info("=" * 60)

        finally:
            browser.close()


def run_status(cfg: dict, notifier):
    """每日检测模式：只登录三丰云扫描所有产品，不发文不提交，发一封状态日报邮件。

    用于 GitHub Actions 每天一次的 cron 检测（sanfengyun_daily_check.yml）。
    不启动博客园 / 不填表 / 不提交，仅读取各产品到期时间与延期状态。
    """
    logger.info("=" * 60)
    logger.info("每日检测模式（只扫描，不提交）")
    logger.info("=" * 60)

    sf_cfg = cfg["sanfengyun"]
    settings = cfg.get("settings", {})
    products = [p for p in cfg.get("products", []) if p.get("enabled", True)]
    _scope = settings.get("scan_scope", "all")
    if _scope in ("vps", "vhost"):
        products = [p for p in products if p.get("ptype") == _scope]
        logger.info(f"[配置] scan_scope={_scope}，只扫描: {[p['name'] for p in products]}")

    scanner = SanfengyunScanner(sf_cfg["phone"], sf_cfg["password"])

    _ensure_display()
    with sync_playwright() as pw:
        browser = launch_browser(pw, headless=False)
        ctx = new_context(browser)
        try:
            scan_results = []
            sf_page = new_page(ctx)
            try:
                if scanner.ensure_login(sf_page):
                    for product in products:
                        result = scanner.scan_product(sf_page, product)
                        scan_results.append(result)
                        logger.info(f"  [{result.get('name')}] form_status={result.get('form_status')} "
                                    f"到期={result.get('expire_time') or '?'}")
                else:
                    logger.error("三丰云登录失败")
                    notifier.send_daily_status([{"name": "登录失败", "form_status": "登录失败"}])
            finally:
                sf_page.close()
        finally:
            browser.close()

    if not scan_results:
        notifier.send_daily_status([])
        return
    notifier.send_daily_status(scan_results)


def run_once(cfg: dict, notifier):
    """单次完整执行（扫描→到期就完整提交）
    关键：can_renew=True 的立即处理；can_renew=False 的只记录可提交时间，不阻塞其他产品
    """
    # 与 run_test 相同结构，但 dry_run=False
    logger.info("=" * 60)
    logger.info("单次执行模式（到期就完整提交）")
    logger.info("=" * 60)

    sf_cfg = cfg["sanfengyun"]
    cb_cfg = cfg["cnblogs"]
    settings = cfg.get("settings", {})
    products = [p for p in cfg.get("products", []) if p.get("enabled", True)]
    # 按 scan_scope 过滤：all=全部, vps=只云服务器, vhost=只虚拟主机
    _scope = settings.get("scan_scope", "all")
    if _scope in ("vps", "vhost"):
        products = [p for p in products if p.get("ptype") == _scope]
        logger.info(f"[配置] scan_scope={_scope}，只扫描: {[p['name'] for p in products]}")

    scanner = SanfengyunScanner(sf_cfg["phone"], sf_cfg["password"])
    publisher = CnblogsPublisher(cb_cfg["email"], cb_cfg["password"], cb_cfg.get("username", ""))
    renewer = SanfengyunRenewer()
    gen = ArticleGenerator()

    kw = settings.get("sanitize_keywords", ["三丰云", "三丰"])
    sanitize_from = kw[0] if kw else "三丰云"
    sanitize_to = kw[1] if len(kw) > 1 else "三丰"

    notifier.send_text(
        f"三丰云自动延期脚本单次执行\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"监控产品: {len(products)}"
    )

    _ensure_display()
    with sync_playwright() as pw:
        browser = launch_browser(pw, headless=False)
        ctx = new_context(browser)

        try:
            # ========== 第一阶段：扫描所有产品 ==========
            scan_results = []
            sf_page = new_page(ctx)
            try:
                if scanner.ensure_login(sf_page):
                    for product in products:
                        result = scanner.scan_product(sf_page, product)
                        scan_results.append(result)
                else:
                    logger.error("三丰云登录失败")
            finally:
                sf_page.close()

            _notify_scan_result(notifier, scan_results)

            # ========== 第二阶段：处理 can_renew=True 且 form_status=ready 的产品 ==========
            # 只对 ready 状态发文；in_review/not_yet/not_activated 都跳过
            skipped = [p for p in scan_results if p["can_renew"] and p.get("form_status") != "ready"]
            for p in skipped:
                logger.info(f"  ⏭️ 跳过 {p['name']}（状态={p.get('form_status')}，不发文）")
            can_renew_products = [p for p in scan_results if p["can_renew"] and p.get("form_status") == "ready"]
            not_ready_products = [p for p in scan_results if not p["can_renew"]]

            # 未到时间的只记录，不阻塞
            for p in not_ready_products:
                wait = _calc_wait_seconds(p)
                logger.info(f"  ⏭️ 跳过 {p['name']}（未到提交时间，下次: {p.get('renew_time') or '未知'}）")
                logger.info(f"     倒计时: {_human_countdown(wait)}")

            if not can_renew_products:
                logger.info("没有可提交的产品（未到时间或审核中），本次不执行发文+提交")
                browser.close()
                return

            # ========== 第三阶段：博客园发文（独立 page，只登录一次，连续发文）==========
            logger.info(f"\n--- 博客园发文（{len(can_renew_products)} 篇）---")
            pub_page = new_page(ctx)
            try:
                if not publisher.login(pub_page):
                    logger.error("博客园登录失败")
                    for p in can_renew_products:
                        notifier.notify_submit_failed(p["name"], "博客园登录失败", product=p)
                    can_renew_products = []
                else:
                    for product in can_renew_products:
                        title, content = gen.generate(product_name=product["name"])
                        pub_ok, article_url, screenshot_path = publisher.publish(
                            pub_page, title, content,
                            sanitize_from=sanitize_from, sanitize_to=sanitize_to,
                        )
                        if not pub_ok or not article_url:
                            product["article_url"] = ""
                            product["screenshot_path"] = ""
                            notifier.notify_submit_failed(product["name"], "博客园发布失败", product=product)
                            continue
                        logger.info(f"  ✅ {product['name']}: {article_url}")
                        product["article_url"] = article_url
                        product["screenshot_path"] = screenshot_path
                        notifier.notify_article_posted(product["name"], article_url, title)
            except Exception as e:
                logger.exception(f"博客园异常: {e}")
            finally:
                pub_page.close()

            # ========== 第四阶段：三丰云填表+提交（sf_page 已登录）==========
            pending_products = []  # 提交成功的产品，进入审核轮询
            for product in can_renew_products:
                if not product.get("article_url"):
                    continue
                logger.info(f"\n--- 三丰云延期提交 {product['name']} ---")

                fill_page = new_page(ctx)
                try:
                    if not scanner.ensure_login(fill_page):
                        notifier.notify_submit_failed(product["name"], "三丰云登录失败", product=product)
                        continue

                    result = renewer.fill_and_submit(
                        fill_page, product["ptype"],
                        product["article_url"], product["screenshot_path"],
                        dry_run=settings.get("dry_run", False),
                        page_url=product.get("page_url") or product.get("url", ""),
                    )
                    if result["success"]:
                        notifier.notify_submit_success(
                            product["name"], product["article_url"],
                            response=result.get("response_text", "")[:300],
                            next_time=result.get("next_renew_time", ""),
                            product=product,
                        )
                        pending_products.append({
                            "name": product["name"],
                            "ptype": product["ptype"],
                            "article_url": product["article_url"],
                            "expire_time": product.get("expire_time", ""),
                            "page_url": product.get("page_url") or product.get("url", ""),
                            "_status": "pending",
                        })
                    else:
                        notifier.notify_submit_failed(product["name"], result.get("response_text", "")[:200], product=product)
                except Exception as e:
                    notifier.notify_submit_failed(product["name"], str(e), product=product)
                finally:
                    fill_page.close()

            # ========== 第五阶段：轮询审核结果（每 5 分钟，最多 ~3 小时）==========
            if pending_products:
                _poll_review(ctx, scanner, notifier, pending_products, settings)

        finally:
            browser.close()


def run_loop(cfg: dict, notifier):
    """持续运行模式（方案2重构版）：
      - 每实例独立保存 next_allow_submit_ts 到 instance_timestamps.json
      - 启动时读 json；文件不存在/缺实例 → 调 scan 初始化
      - 60s 分片 sleep；触发条件：now >= next_ts + TRIGGER_DELAY(60)
      - 触发后重新从 DOM 拉真实状态（json 只是倒计时记录，DOM 为准）
      - 提交完成写回 json，重置倒计时；未到时间同样更新 json，跳过
    """
    logger.info("=" * 60)
    logger.info("三丰云自动延期脚本 - 持续运行模式 (v4 方案2)")
    logger.info("=" * 60)

    sf_cfg = cfg["sanfengyun"]
    cb_cfg = cfg["cnblogs"]
    settings = cfg.get("settings", {})
    products = [p for p in cfg.get("products", []) if p.get("enabled", True)]
    # 按 scan_scope 过滤：all=全部, vps=只云服务器, vhost=只虚拟主机
    _scope = settings.get("scan_scope", "all")
    if _scope in ("vps", "vhost"):
        products = [p for p in products if p.get("ptype") == _scope]
        logger.info(f"[配置] scan_scope={_scope}，只扫描: {[p['name'] for p in products]}")

    scanner = SanfengyunScanner(sf_cfg["phone"], sf_cfg["password"])
    publisher = CnblogsPublisher(cb_cfg["email"], cb_cfg["password"], cb_cfg.get("username", ""))
    renewer = SanfengyunRenewer()
    gen = ArticleGenerator()

    kw = settings.get("sanitize_keywords", ["三丰云", "三丰"])
    sanitize_from = kw[0] if kw else "三丰云"
    sanitize_to = kw[1] if len(kw) > 1 else "三丰"

    # ---- 启动通知 ----
    notifier.send_text(
        f"三丰云自动延期脚本 v4 已启动\n"
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"持久化: {STATE_FILE}\n"
        f"监控产品: {len(products)} 个（每实例独立倒计时）\n"
        f"触发缓冲: {TRIGGER_DELAY}s  |  分片 sleep: {SLEEP_SLICE}s"
    )

    # =================================================================
    # 辅助：把产品配置扫描一遍，拿到 next_allow_submit_ts，写回 json
    # =================================================================
    def _initialize_or_refresh_state(ctx) -> dict:
        """
        对所有 product 做一次 DOM 扫描，更新本地 state，返回更新后的 state。
        如果 json 已存在且包含全部 key，则跳过；否则强制全量扫描。
        """
        current_state = load_instance_state()
        all_keys = {_instance_key(p) for p in products}
        missing_keys = all_keys - set(current_state.keys())

        if not missing_keys and current_state:
            logger.info(f"[状态] 本地 json 已完整（{len(current_state)} 个实例），跳过初始化扫描")
            return current_state

        logger.info(f"[状态] 缺失实例: {missing_keys}，执行 DOM 全量扫描初始化...")
        page = new_page(ctx)
        try:
            if not scanner.ensure_login(page):
                logger.error("[状态] 登录失败，无法初始化")
                return current_state
            for product in products:
                key = _instance_key(product)
                status = scanner.scan_product(page, product)
                next_ts = _next_ts_from_status(status)
                current_state[key] = {"next_allow_submit_ts": next_ts}
                logger.info(f"[状态] {key} -> next_ts={next_ts} ({datetime.fromtimestamp(next_ts)})")
            save_instance_state(current_state)
            logger.info(f"[状态] ✅ 已持久化 {len(current_state)} 个实例时间戳")
        finally:
            page.close()
        return current_state

    # =================================================================
    # 辅助：从 scan_product 返回的 dict 推导 next_allow_submit_ts
    # =================================================================
    def _next_ts_from_status(status: dict) -> int:
        """
        DOM 扫描结果 → Unix 时间戳
          - form_status=not_activated → now + 24h（产品未开通，一天后再查）
          - form_status=in_review      → now + 2h（审核中，2h 后再扫）
          - form_status=ready          → 当前时间（现在就可以触发）
          - 其他                       → 解析 renew_time；解析不到兜底 now+30min
        """
        fs = status.get("form_status", "")
        if fs == "not_activated":
            return int(time.time()) + 24 * 3600
        if fs == "in_review":
            return int(time.time()) + 2 * 3600
        if status.get("can_renew") or fs == "ready":
            return int(time.time())
        renew_str = status.get("renew_time", "")
        if renew_str:
            try:
                dt = datetime.strptime(renew_str.replace("/", "-"), "%Y-%m-%d %H:%M:%S")
                return int(dt.timestamp())
            except Exception:
                pass
        return int(time.time()) + 1800  # 兜底 30 分钟后重试

    # =================================================================
    # 辅助：写回 next_ts 到 json
    # =================================================================
    def _save_next_ts(key: str, next_ts: int):
        s = load_instance_state()
        s[key] = {"next_allow_submit_ts": next_ts}
        save_instance_state(s)
        logger.info(f"[执行] ✅ 下次扫描: {datetime.fromtimestamp(next_ts)}")

    # =================================================================
    # 辅助：执行单个产品完整流程（博客园发文 → 三丰云提交）
    # =================================================================
    def _execute_single_product(ctx, product, notifier_obj) -> str:
        """
        返回: "ok" | "skip" | "fail"
        内部会更新 json 中该实例的 next_allow_submit_ts
        🔒 核心逻辑：
          1. DOM 先查 form_status：
             - ready         → 去博客园发帖 → 三丰云提交 → next_ts=now+4h（等审核）
             - in_review     → 不发帖不提交，next_ts=now+2h（2h 后再扫）
             - not_activated → 不发帖不提交，next_ts=now+24h（产品未开通，一天后再查）
             - not_yet       → 不发帖不提交，next_ts=renew_time（等页面说的时间点）
          2. 任何分支都发钉钉：本次结果 + 下次脚本执行时间
          3. 任何失败分支也写回 next_ts=now+30min，避免死循环触发
          4. 绝对不能先发帖再去检查入口 —— 反过来！
        """
        key = _instance_key(product)
        logger.info(f"\n{'='*60}")
        logger.info(f"⏰ 触发执行: {product['name']} ({key})")
        logger.info(f"{'='*60}")

        # -------------------- Phase A: DOM 先查表单状态 --------------------
        sf_page = new_page(ctx)
        try:
            if not scanner.ensure_login(sf_page):
                logger.error("[执行] 三丰云登录失败")
                next_ts = int(time.time()) + 30 * 60  # 30min 后重试
                s = load_instance_state()
                s[key] = {"next_allow_submit_ts": next_ts}
                save_instance_state(s)
                notifier_obj.notify_submit_failed(product["name"], "三丰云登录失败", next_run_ts=next_ts)
                return "fail"
            status = scanner.scan_product(sf_page, product)
            form_status = status.get("form_status", "not_yet")
            logger.info(f"[执行] DOM 表单状态: {form_status}  expire={status.get('expire_time')}  renew_time={status.get('renew_time')}")
        finally:
            sf_page.close()

        # 扫描结果通知：让用户看到 DOM 扫描到了什么
        notifier_obj.notify_scan([{
            "name": product["name"],
            "form_status": form_status,
            "expire_time": status.get("expire_time", ""),
            "renew_time": status.get("renew_time", ""),
            "next_trigger": "",
        }])

        # -------------------- Phase B: 根据 form_status 分支 --------------------
        # --- 分支 1: ready → 发帖 + 提交 → 冷却 4h ---
        if form_status == "ready":
            logger.info("[执行] ✅ DOM 确认有可提交表单 → 进入博客园发文 + 三丰云提交流程")

            # 博客园发文
            pub_page = new_page(ctx)
            try:
                if not publisher.login(pub_page):
                    next_ts = int(time.time()) + 30 * 60
                    _save_next_ts(key, next_ts)
                    notifier_obj.notify_submit_failed(product["name"], "博客园登录失败", next_run_ts=next_ts)
                    return "fail"
                title, content = gen.generate(product_name=product["name"])
                pub_ok, article_url, screenshot_path = publisher.publish(
                    pub_page, title, content,
                    sanitize_from=sanitize_from, sanitize_to=sanitize_to,
                )
                if not pub_ok or not article_url:
                    next_ts = int(time.time()) + 30 * 60
                    _save_next_ts(key, next_ts)
                    notifier_obj.notify_submit_failed(product["name"], "博客园发布失败", next_run_ts=next_ts)
                    return "fail"
                logger.info(f"[执行] ✅ 博文已发布: {article_url}")
                notifier_obj.notify_article_posted(product["name"], article_url, title)
            except Exception as e:
                logger.exception(f"[执行] 博客园异常: {e}")
                next_ts = int(time.time()) + 30 * 60
                _save_next_ts(key, next_ts)
                notifier_obj.notify_submit_failed(product["name"], f"博客园异常: {e}", next_run_ts=next_ts)
                return "fail"
            finally:
                pub_page.close()

            # 三丰云填表 + 提交
            fill_page = new_page(ctx)
            try:
                if not scanner.ensure_login(fill_page):
                    next_ts = int(time.time()) + 30 * 60
                    _save_next_ts(key, next_ts)
                    notifier_obj.notify_submit_failed(product["name"], "三丰云登录失败", next_run_ts=next_ts)
                    return "fail"
                result = renewer.fill_and_submit(
                    fill_page, product["ptype"],
                    article_url, screenshot_path,
                    dry_run=settings.get("dry_run", False),
                    page_url=product.get("url", ""),
                )
                if result["success"]:
                    logger.info("[执行] ✅ 延期提交成功 → 冷却 4h 等审核")
                    next_ts = int(time.time()) + 4 * 3600
                    notifier_obj.notify_submit_success(
                        product["name"], article_url,
                        response=result.get("response_text", "")[:300],
                        next_time=result.get("next_renew_time", ""),
                        next_run_ts=next_ts,
                        product=product,
                    )
                else:
                    logger.error(f"[执行] ❌ 延期提交失败 → 冷却 30min 重试: {result.get('response_text','')[:200]}")
                    next_ts = int(time.time()) + 30 * 60
                    notifier_obj.notify_submit_failed(product["name"],
                        f"延期提交失败: {result.get('response_text','')[:200]}",
                        next_run_ts=next_ts)
            except Exception as e:
                logger.exception(f"[执行] 三丰云异常: {e}")
                next_ts = int(time.time()) + 30 * 60
                _save_next_ts(key, next_ts)
                notifier_obj.notify_submit_failed(product["name"], str(e), next_run_ts=next_ts)
                return "fail"
            finally:
                fill_page.close()

            # 写回 json
            _save_next_ts(key, next_ts)
            return "ok"

        # --- 分支 2: in_review → 等 2h 再来扫，循环直到得出下次可提交时间 ---
        elif form_status == "in_review":
            logger.info("[执行] 🟡 DOM 检测到审核中 → 冷却 2h 后重新扫描")
            next_ts = int(time.time()) + 2 * 3600
            _save_next_ts(key, next_ts)
            next_str = datetime.fromtimestamp(next_ts).strftime("%Y-%m-%d %H:%M:%S")
            notifier_obj.notify_waiting(
                product["name"],
                f"延期申请审核中，脚本将在 {next_str} 再次扫描",
                next_run_ts=next_ts,
            )
            return "skip"

        # --- 分支 3: not_activated → 产品未开通，等 24h 再扫一次 ---
        elif form_status == "not_activated":
            logger.info("[执行] ⚫ DOM 检测到产品未开通 → 24h 后再扫")
            next_ts = int(time.time()) + 24 * 3600
            _save_next_ts(key, next_ts)
            notifier_obj.notify_waiting(
                product["name"], "产品未开通，24h 后再检查",
                next_run_ts=next_ts,
            )
            return "skip"

        # --- 分支 4: not_yet → 用 renew_time / expire_time 算下次 ---
        else:
            renew_time = status.get("renew_time", "")
            if renew_time:
                try:
                    dt = datetime.strptime(renew_time.replace("/", "-"), "%Y-%m-%d %H:%M:%S")
                    next_ts = int(dt.timestamp()) + TRIGGER_DELAY
                except Exception:
                    next_ts = int(time.time()) + 30 * 60
            else:
                next_ts = int(time.time()) + 30 * 60  # 兜底 30min 再扫
            _save_next_ts(key, next_ts)
            logger.info(f"[执行] ⏸ 未到时间 → 下次扫描: {datetime.fromtimestamp(next_ts)}")
            notifier_obj.notify_waiting(
                product["name"],
                f"未到可提交时间，下次扫描 {datetime.fromtimestamp(next_ts).strftime('%m-%d %H:%M')}",
                next_run_ts=next_ts,
            )
            return "skip"

    # =================================================================
    # 主循环
    # =================================================================
    first_run = True
    last_urgent_hint = {}   # 每个实例只推送一次 "即将触发" 提醒，避免每轮都推

    while True:
        try:
            now_ts = int(time.time())

            # 先读 JSON 判断是否需要触发（不需要开浏览器）
            state = load_instance_state()
            all_keys = {_instance_key(p) for p in products}
            missing_keys = all_keys - set(state.keys())

            # 判断是否有实例到达触发条件： now >= next_ts + TRIGGER_DELAY
            # next_ts 来自本地 json，+60s 缓冲是为了避开三丰云服务器与本地机器的时间差
            need_trigger_keys = []
            for product in products:
                key = _instance_key(product)
                item = state.get(key, {})
                next_ts = item.get("next_allow_submit_ts", 0)
                remain = next_ts + TRIGGER_DELAY - now_ts

                # 到点触发：剩余秒数 <=0
                if remain <= 0:
                    logger.info(f"[判断] {key} 剩余 {remain}s <= 0 → 触发")
                    need_trigger_keys.append(key)

                # 到点前 30 分钟提醒（每实例每轮只推一次，触发后清除标记）
                elif 0 < remain <= URGENT_HINT_THRESHOLD:
                    if key not in last_urgent_hint:
                        human = _human_countdown(remain)
                        next_trigger = datetime.fromtimestamp(next_ts + TRIGGER_DELAY).strftime("%m-%d %H:%M")
                        logger.info(f"[提醒] {product['name']} 即将触发，倒计时 {human}")
                        notifier.notify_countdown_urgent(product['name'], next_trigger, human)
                        last_urgent_hint[key] = True

            # 无需触发也无需初始化 → 只打日志，不开浏览器
            if not need_trigger_keys and not missing_keys:
                parts = []
                for product in products:
                    key = _instance_key(product)
                    item = state.get(key, {})
                    next_ts = item.get("next_allow_submit_ts", 0) + TRIGGER_DELAY
                    parts.append(f"{product['name']} → {datetime.fromtimestamp(next_ts).strftime('%m-%d %H:%M')}")
                logger.info(f"[等待中] {' | '.join(parts)}")
                time.sleep(SLEEP_SLICE)
                continue

            # 需要开浏览器：触发执行 或 初始化扫描
            _ensure_display()
            with sync_playwright() as pw:
                browser = launch_browser(pw, headless=False)
                ctx = new_context(browser)

                # 初始化（第一次或 json 不完整时做）
                if missing_keys:
                    state = _initialize_or_refresh_state(ctx)

                if need_trigger_keys:
                    logger.info(f"\n========⏰ 检测到 {len(need_trigger_keys)} 个实例到达触发时间 ========")
                    for product in products:
                        key = _instance_key(product)
                        if key in need_trigger_keys:
                            next_trigger = datetime.fromtimestamp(
                                state.get(key, {}).get("next_allow_submit_ts", 0) + TRIGGER_DELAY
                            ).strftime("%Y-%m-%d %H:%M:%S")
                            notifier.notify_trigger_fired(product["name"], next_trigger)
                            break  # 推一次就够了

                    for product in products:
                        key = _instance_key(product)
                        if key in need_trigger_keys:
                            _execute_single_product(ctx, product, notifier)
                            # 执行完后刷新 state（更新了 json）
                            state = load_instance_state()
                            # 清除提醒标记，下轮倒计时重新进入 30 分钟窗口时再推
                            last_urgent_hint.pop(key, None)

                    logger.info("✅ 一轮触发执行完毕，回到等待循环\n")

                browser.close()

            # 分片 sleep 60s，下一轮循环
            time.sleep(SLEEP_SLICE)

        except KeyboardInterrupt:
            logger.info("收到 Ctrl+C，退出")
            notifier.send_text("三丰云自动延期脚本已停止（Ctrl+C）")
            break
        except Exception as e:
            logger.exception(f"[循环] 异常: {e}")
            notifier.notify_error(f"循环异常: {e}")
            time.sleep(SLEEP_SLICE)


# ==========================================================================
# 辅助函数
# ==========================================================================

def _calc_wait_seconds(status: dict) -> int:
    """根据延期状态计算应等待的秒数"""
    if status["can_renew"]:
        return 0

    renew_time_str = status.get("renew_time", "")
    if renew_time_str:
        try:
            renew_time_str = renew_time_str.replace("/", "-")
            renew_dt = datetime.strptime(renew_time_str, "%Y-%m-%d %H:%M:%S")
            wait = (renew_dt - datetime.now()).total_seconds()
            if wait > 0:
                return int(wait)
        except Exception:
            pass

    return 1800  # 兜底 30 分钟


def _human_countdown(seconds: int) -> str:
    if seconds <= 0:
        return "现在"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}天")
    if hours > 0:
        parts.append(f"{hours}时")
    if mins > 0:
        parts.append(f"{mins}分")
    if not parts:
        parts.append(f"{secs}秒")
    return "".join(parts)


def _wait_until(renew_time_str: str):
    """等到某个时间点"""
    if not renew_time_str:
        return
    try:
        renew_dt = datetime.strptime(renew_time_str.replace("/", "-"), "%Y-%m-%d %H:%M:%S")
        wait = (renew_dt - datetime.now()).total_seconds()
        if wait > 0:
            logger.info(f"  等待 {_human_countdown(int(wait))} 直到 {renew_time_str}")
            time.sleep(wait + 10)
    except Exception:
        time.sleep(1800)


def _poll_review(ctx, scanner, notifier, pending_products, settings):
    """提交延期后轮询审核结果（每 5 分钟扫一次，直到通过/失败/超时）。

    pending_products: list[dict] 已成功提交的产品，含 name/ptype/article_url/expire_time
    审核通过判据：form_status 从 in_review/ready 变为 not_yet（页面显示"未到提交时间"）
    超时：轮询超过 max_polls 次仍未通过 → 发"延期失败(超时)"邮件
    """
    poll_interval = int(settings.get("review_poll_interval", 300))   # 默认 5 分钟
    max_polls = int(settings.get("review_max_polls", 36))            # 默认 36 次 ≈ 3 小时
    dry_run = bool(settings.get("dry_run", False))
    if dry_run:
        logger.info("dry_run 模式：跳过审核轮询")
        return

    logger.info(f"\n{'=' * 60}")
    logger.info(f"开始轮询审核结果（每 {poll_interval} 秒一次，最多 {max_polls} 次）")
    logger.info("=" * 60)

    poll_page = None
    try:
        for i in range(1, max_polls + 1):
            remaining = [p for p in pending_products if p.get("_status") not in ("passed", "failed")]
            if not remaining:
                break
            logger.info(f"\n--- 审核轮询 {i}/{max_polls}（剩余 {len(remaining)} 个产品）---")

            if poll_page is None:
                poll_page = new_page(ctx)
                if not scanner.ensure_login(poll_page):
                    logger.error("轮询登录失败")
                    for p in remaining:
                        p["_status"] = "failed"
                        notifier.send_review_failed(p["name"], "轮询登录失败", article_url=p.get("article_url", ""))
                    break

            for p in remaining:
                try:
                    # 重新扫描该产品页面，拿最新 form_status / expire_time
                    p_url = p.get("page_url") or p.get("url", "")
                    status = scanner.scan_product(poll_page, {
                        "name": p["name"], "ptype": p["ptype"], "url": p_url,
                    })
                    fstatus = status.get("form_status", "")
                    new_expire = status.get("expire_time", "")
                    logger.info(f"  [{p['name']}] form_status={fstatus} 到期={new_expire or '?'}")

                    if fstatus == "not_yet":
                        # 审核通过：到期时间已顺延（可能仍是旧值，此时提示以控制台为准）
                        p["_status"] = "passed"
                        p["new_expire"] = new_expire
                        days = {"vps": 5, "vhost": 30}.get(p.get("ptype", ""), 0)
                        notifier.send_review_success(
                            p["name"], days,
                            p.get("expire_time", ""), new_expire,
                            article_url=p.get("article_url", ""),
                        )
                    elif fstatus == "in_review":
                        logger.info(f"  [{p['name']}] 仍在审核中，继续等待")
                    elif fstatus == "ready":
                        logger.info(f"  [{p['name']}] 仍显示可提交（审核结果未生效），继续等待")
                    elif fstatus == "not_activated":
                        p["_status"] = "failed"
                        notifier.send_review_failed(p["name"], "产品未开通", article_url=p.get("article_url", ""))
                except Exception as e:
                    logger.error(f"  [{p['name']}] 轮询扫描异常: {e}")
                    # 单个产品异常不致命，留给下一轮

            if all(p.get("_status") in ("passed", "failed") for p in pending_products):
                break
            if i < max_polls:
                logger.info(f"  ⏳ 等待 {poll_interval} 秒后继续轮询...")
                time.sleep(poll_interval)

        # 轮询结束：仍有未通过的 → 超时
        for p in pending_products:
            if p.get("_status") not in ("passed", "failed"):
                p["_status"] = "failed"
                notifier.send_review_failed(p["name"], "审核超时未确认（请登录三丰云控制台查看）",
                                            article_url=p.get("article_url", ""))
    finally:
        if poll_page is not None:
            try:
                poll_page.close()
            except Exception:
                pass


def _notify_scan_result(notifier, results: list):
    """发送扫描结果到钉钉（美观 Markdown 格式）"""
    products_out = []
    for r in results:
        # 计算下次触发时间（can_renew=True 则 now+TRIGGER_DELAY，否则 renew_time+TRIGGER_DELAY）
        if r["can_renew"]:
            next_trigger = datetime.fromtimestamp(int(time.time()) + TRIGGER_DELAY).strftime("%m-%d %H:%M")
        else:
            renew_str = r.get("renew_time", "")
            try:
                from datetime import datetime as _dt
                ts = int(_dt.strptime(renew_str.replace("/", "-"), "%Y-%m-%d %H:%M:%S").timestamp())
                next_trigger = datetime.fromtimestamp(ts + TRIGGER_DELAY).strftime("%m-%d %H:%M")
            except Exception:
                next_trigger = "?"
        products_out.append({
            "name": r["name"],
            "form_status": r.get("form_status", "not_yet"),
            "expire_time": r.get("expire_time", ""),
            "renew_time": r.get("renew_time", ""),
            "next_trigger": next_trigger,
        })
    notifier.notify_scan(products_out)


# ==========================================================================
# 入口
# ==========================================================================

def main():
    parser = argparse.ArgumentParser(description="三丰云免费产品自动延期脚本 v3")
    parser.add_argument("--test", action="store_true", help="测试模式：扫描→发文→填表(不提交)")
    parser.add_argument("--once", action="store_true", help="单次执行：完整流程（会提交）")
    parser.add_argument("--status", action="store_true", help="每日检测模式：只扫描发状态邮件，不提交")
    parser.add_argument("--config", type=str, default="", help="自定义配置文件路径")
    args = parser.parse_args()

    # 加载配置
    global BASE_DIR, SCREENSHOT_DIR
    if args.config:
        cfg_path = Path(args.config)
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        BASE_DIR = cfg_path.parent
        SCREENSHOT_DIR = BASE_DIR / "screenshots"
        SCREENSHOT_DIR.mkdir(exist_ok=True)
    else:
        cfg = load_config()

    setup_logging(cfg.get("logging", {}))

    # 邮件通知（替代钉钉）：按阶段发送——提交成功发"已提交"，审核通过发"延期成功"，失败发"延期失败"；无动作时这里补发"检查"邮件
    notifier = EmailNotifier()

    # 路由模式
    if args.test:
        run_test(cfg, notifier)
    elif args.status:
        run_status(cfg, notifier)
    elif args.once:
        run_once(cfg, notifier)
    else:
        run_loop(cfg, notifier)

    # 单次/测试模式结束后发送汇总邮件（持续模式自身循环内不调用）
    if args.test or args.once:
        notifier.send_summary()


if __name__ == "__main__":
    main()

