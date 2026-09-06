#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三丰云自动延期 - 邮件通知模块（替代原钉钉通知）
每次运行结束后发送一封汇总结果邮件（免登录确认延期成败）。

标题：
  【三丰云延期成功】YYYY-MM-DD HH:MM:SS   （至少一个产品延期成功且无失败）
  【三丰云延期失败】YYYY-MM-DD HH:MM:SS   （至少一个产品延期失败）
  【三丰云延期检查】YYYY-MM-DD HH:MM:SS   （本次无延期操作：全部未到时间/审核中）

正文只含结果摘要（不含验证过程日志）。
"""

import os
import re
import smtplib
import logging
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText

logger = logging.getLogger("sanfengyun.email")


def _bj_now() -> str:
    """北京时间，精确到秒"""
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")


def _add_days(date_str: str, days: int) -> str:
    """把 '2026-09-12 08:00:00' 加上 days 天，返回同格式；解析失败返回 ''"""
    if not date_str:
        return ""
    date_str = str(date_str).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return (dt + timedelta(days=days)).strftime(fmt)
        except ValueError:
            continue
    return ""


# 各产品类型单次延期天数（免费云服务器 +5天/次，免费虚拟主机 +30天/次）
EXTEND_DAYS = {"vps": 5, "vhost": 30}


class EmailNotifier:
    """SMTP 邮件通知：收集本运行各产品结果，结束时统一发送汇总邮件"""

    def __init__(self):
        self.smtp_host = os.getenv("SMTP_HOST", "") or os.getenv("SMTP_SERVER", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "465"))
        self.smtp_user = os.getenv("SMTP_USER", "") or os.getenv("EMAIL_USERNAME", "")
        self.smtp_pass = os.getenv("SMTP_PASS", "") or os.getenv("EMAIL_PASSWORD", "")
        self.smtp_to = os.getenv("SMTP_TO", "") or os.getenv("RECEIVER_EMAIL", self.smtp_user)
        # name -> {status, product_name, days, old_expire, new_expire, article_url, error, reason}
        self.results = {}

    # ------------------------------------------------------------------
    # 结果收集（过程性通知只写日志，不单独发邮件，避免轰炸）
    # ------------------------------------------------------------------
    def send_text(self, content: str) -> bool:
        logger.info(f"[通知] {content}")
        return True

    def notify_scan(self, products: list):
        for p in products:
            self.results.setdefault(p.get("name", "?"), {
                "status": "未到时间/审核中",
                "product_name": p.get("name", "?"),
                "days": 0,
                "old_expire": p.get("expire_time", ""),
                "new_expire": "",
                "article_url": "",
                "error": "",
                "reason": "未到可提交时间或审核中",
            })

    def notify_countdown_urgent(self, product_name: str, next_time: str, remain_str: str):
        logger.info(f"[通知] {product_name} 即将触发: {next_time} ({remain_str})")

    def notify_trigger_fired(self, product_name: str, next_time: str):
        logger.info(f"[通知] {product_name} 已触发: {next_time}")

    def notify_article_posted(self, product_name: str, article_url: str, title: str):
        logger.info(f"[通知] {product_name} 文章已发布: {article_url}")

    def notify_submit_success(self, product_name: str, article_url: str = "",
                              response: str = "", next_time: str = "",
                              next_run_ts: int = 0, product: dict = None):
        """延期提交成功：记录成功信息（含延期天数与到期时间起止）"""
        product = product or {}
        ptype = product.get("ptype", "")
        days = EXTEND_DAYS.get(ptype, 0)
        old_expire = product.get("expire_time", "")
        new_expire = _add_days(old_expire, days) if days else ""
        self.results[product_name] = {
            "status": "成功",
            "product_name": product_name,
            "days": days,
            "old_expire": old_expire,
            "new_expire": new_expire,
            "article_url": article_url,
            "error": "",
            "reason": "延期申请已提交，等待三丰云审核",
        }
        logger.info(f"[通知] {product_name} 延期成功，+{days}天，到期 {old_expire} → {new_expire}")

    def notify_submit_failed(self, product_name: str, error: str, next_run_ts: int = 0,
                             product: dict = None):
        product = product or {}
        item = self.results.get(product_name, {
            "status": "失败", "product_name": product_name,
            "days": 0, "old_expire": product.get("expire_time", ""),
            "new_expire": "", "article_url": "",
        })
        item.update({"status": "失败", "error": error, "reason": "延期失败"})
        self.results[product_name] = item
        logger.error(f"[通知] {product_name} 延期失败: {error}")

    def notify_waiting(self, product_name: str, reason: str, next_run_ts: int = 0):
        item = self.results.setdefault(product_name, {
            "status": "跳过", "product_name": product_name,
            "days": 0, "old_expire": "", "new_expire": "",
            "article_url": "", "error": "", "reason": reason,
        })
        item["reason"] = reason
        self.results[product_name] = item
        logger.info(f"[通知] {product_name} 跳过: {reason}")

    def notify_error(self, error: str):
        logger.error(f"[通知] 脚本异常: {error}")
        if not self.results:
            self.results["脚本异常"] = {
                "status": "失败", "product_name": "脚本异常", "days": 0,
                "old_expire": "", "new_expire": "", "article_url": "",
                "error": error, "reason": "脚本异常",
            }

    # ------------------------------------------------------------------
    # 汇总发送
    # ------------------------------------------------------------------
    def build_body(self) -> str:
        lines = [
            "三丰云免费产品自动延期",
            "==============================",
            f"延期时间：{_bj_now()}",
            "",
        ]
        if not self.results:
            lines.append("本次无产品延期操作（未到可提交时间或审核中）。")
        for name, r in self.results.items():
            lines.append(f"[{r.get('product_name', name)}]")
            lines.append(f"  延期状态：{r.get('status', '?')}")
            if r.get("status") == "成功":
                lines.append(f"  延期天数：{r.get('days', 0)} 天")
                old_e = r.get("old_expire") or "--"
                new_e = r.get("new_expire") or "--"
                lines.append(f"  到期时间：{old_e} → {new_e}")
                if r.get("article_url"):
                    lines.append(f"  博文链接：{r['article_url']}")
            elif r.get("error"):
                lines.append(f"  失败原因：{r.get('error')}")
            else:
                lines.append(f"  说明：{r.get('reason') or '未到可提交时间或审核中'}")
            lines.append("")
        lines.append("==============================")
        lines.append("(到期时间以三丰云控制台实际显示为准；延期需工作时间审核)")
        return "\n".join(lines)

    def send_summary(self) -> bool:
        """发送本运行汇总邮件。返回是否发送成功（未配置则打印并返回 False）。"""
        if not all([self.smtp_host, self.smtp_user, self.smtp_pass, self.smtp_to]):
            logger.info("[邮件] 未完整配置 SMTP，本次不发送。需 SMTP_HOST / SMTP_USER / SMTP_PASS / SMTP_TO")
            logger.info(f"[邮件] 收件人应为: {self.smtp_to or '(未设置 SMTP_TO)'}")
            return False

        # 标题状态：任一失败→失败；否则任一成功→成功；否则检查
        statuses = [r.get("status") for r in self.results.values()]
        if "失败" in statuses:
            state = "失败"
        elif "成功" in statuses:
            state = "成功"
        else:
            state = "检查"
        subject = f"【三丰云延期{state}】{_bj_now()}"
        body = self.build_body()

        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["From"] = self.smtp_user
            msg["To"] = self.smtp_to
            msg["Subject"] = subject

            if self.smtp_port == 465:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port)
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port)
                server.starttls()
            with server:
                server.login(self.smtp_user, self.smtp_pass)
                server.send_message(msg)
            logger.info(f"[邮件] 发送成功: {self.smtp_to}")
            logger.info(f"[邮件] 主题: {subject}")
            logger.info(f"[邮件] 正文:\n{body}")
            return True
        except Exception as e:
            logger.error(f"[邮件] 发送失败: {e}")
            return False
