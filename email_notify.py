#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三丰云自动延期 - 邮件通知模块（替代原钉钉通知）

邮件分三阶段（每轮运行按需发送）：
  1. 【三丰云延期已提交】 延期申请提交成功，立即发送（正文带博文链接）
  2. 【三丰云延期成功】   审核通过，到期时间从 A 延期到 B（正式结果邮件）
  3. 【三丰云延期失败】   提交失败 / 审核失败 / 超时未确认
  4. 【三丰云延期检查】   本轮无任何延期操作（全部未到时间/审核中/未开通）

正文只含结果摘要（不含验证过程日志）。
"""

import os
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
    """SMTP 邮件通知：按阶段独立发送，不攒到运行结束"""

    def __init__(self):
        self.smtp_host = os.getenv("SMTP_HOST", "") or os.getenv("SMTP_SERVER", "")
        self.smtp_port = int(os.getenv("SMTP_PORT", "465"))
        self.smtp_user = os.getenv("SMTP_USER", "") or os.getenv("EMAIL_USERNAME", "")
        self.smtp_pass = os.getenv("SMTP_PASS", "") or os.getenv("EMAIL_PASSWORD", "")
        self.smtp_to = os.getenv("SMTP_TO", "") or os.getenv("RECEIVER_EMAIL", self.smtp_user)
        # 标记本轮是否发过"动作邮件"（已提交/成功/失败）。若无动作，结束时发"检查"邮件
        self._sent_action = False

    # ------------------------------------------------------------------
    # 底层发送
    # ------------------------------------------------------------------
    def _send(self, subject: str, body: str) -> bool:
        """发送一封邮件；未配置 SMTP 或发送失败返回 False（不抛异常）。"""
        if not all([self.smtp_host, self.smtp_user, self.smtp_pass, self.smtp_to]):
            logger.info("[邮件] 未完整配置 SMTP，本次不发送。需 SMTP_HOST / SMTP_USER / SMTP_PASS / SMTP_TO")
            logger.info(f"[邮件] 收件人应为: {self.smtp_to or '(未设置 SMTP_TO)'}")
            return False
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

    # ------------------------------------------------------------------
    # 三阶段邮件（供主程序按阶段调用）
    # ------------------------------------------------------------------
    def send_submitted(self, product_name: str, article_url: str = "", ptype: str = ""):
        """阶段1：延期申请已提交（提交成功后立即发，带博文链接）"""
        self._sent_action = True
        subject = f"【三丰云延期已提交】{_bj_now()}"
        body = "\n".join([
            "三丰云免费产品自动延期",
            "==============================",
            f"已提交延期申请，等待三丰云审核",
            f"提交时间：{_bj_now()}",
            "",
            f"[{product_name}]",
            f"  状态：已提交（待审核）",
            f"  延期天数：{EXTEND_DAYS.get(ptype, 0)} 天",
        ])
        if article_url:
            body += f"\n  博文链接：{article_url}"
        body += "\n\n=============================="
        body += "\n(审核通过后约 5 分钟内会再发一封结果邮件)"
        self._send(subject, body)

    def send_review_success(self, product_name: str, days: int, old_expire: str,
                            new_expire: str, article_url: str = ""):
        """阶段2：审核通过，到期时间从 A 延期到 B"""
        self._sent_action = True
        subject = f"【三丰云延期成功】{_bj_now()}"
        body = "\n".join([
            "三丰云免费产品自动延期",
            "==============================",
            f"延期审核通过！",
            f"确认时间：{_bj_now()}",
            "",
            f"[{product_name}]",
            f"  延期状态：成功",
            f"  延期天数：{days} 天",
            f"  到期时间：{old_expire or '--'} → {new_expire or '--'}",
        ])
        if article_url:
            body += f"\n  博文链接：{article_url}"
        body += "\n\n=============================="
        body += "\n(到期时间以三丰云控制台实际显示为准)"
        self._send(subject, body)

    def send_review_failed(self, product_name: str, reason: str, article_url: str = ""):
        """阶段3：提交失败 / 审核失败 / 超时未确认"""
        self._sent_action = True
        subject = f"【三丰云延期失败】{_bj_now()}"
        body = "\n".join([
            "三丰云免费产品自动延期",
            "==============================",
            f"延期处理未成功",
            f"时间：{_bj_now()}",
            "",
            f"[{product_name}]",
            f"  延期状态：失败",
            f"  原因：{reason}",
        ])
        if article_url:
            body += f"\n  博文链接：{article_url}"
        body += "\n\n=============================="
        body += "\n(请登录三丰云控制台手动处理)"
        self._send(subject, body)

    def send_check(self, summary: str = ""):
        """阶段4：本轮无延期操作（未到时间/审核中/未开通等）"""
        if self._sent_action:
            return  # 已发过动作邮件，不再补发检查邮件
        subject = f"【三丰云延期检查】{_bj_now()}"
        body = "\n".join([
            "三丰云免费产品自动延期",
            "==============================",
            f"本次检查未执行延期操作",
            f"检查时间：{_bj_now()}",
            "",
            f"说明：{summary or '所有产品均未到可提交时间，或审核中、未开通'}",
            "==============================",
            "(到期时间以三丰云控制台实际显示为准)",
        ])
        self._send(subject, body)

    # ------------------------------------------------------------------
    # 兼容旧方法名（主程序 run_once / run_loop 仍按这些名字调用）
    # ------------------------------------------------------------------
    def send_text(self, content: str) -> bool:
        logger.info(f"[通知] {content}")
        return True

    def notify_scan(self, products: list):
        # 只记录日志；结果邮件由主程序按阶段发
        for p in products:
            logger.info(f"[扫描通知] {p.get('name', '?')} 状态={p.get('form_status', '?')}")

    def notify_countdown_urgent(self, product_name: str, next_time: str, remain_str: str):
        logger.info(f"[通知] {product_name} 即将触发: {next_time} ({remain_str})")

    def notify_trigger_fired(self, product_name: str, next_time: str):
        logger.info(f"[通知] {product_name} 已触发: {next_time}")

    def notify_article_posted(self, product_name: str, article_url: str, title: str):
        logger.info(f"[通知] {product_name} 文章已发布: {article_url}")

    def notify_submit_success(self, product_name: str, article_url: str = "",
                              response: str = "", next_time: str = "",
                              next_run_ts: int = 0, product: dict = None):
        """提交成功 → 立即发"已提交"邮件（带博文链接）"""
        product = product or {}
        self.send_submitted(product_name, article_url, ptype=product.get("ptype", ""))
        logger.info(f"[通知] {product_name} 延期申请已提交")

    def notify_submit_failed(self, product_name: str, error: str, next_run_ts: int = 0,
                             product: dict = None):
        product = product or {}
        self.send_review_failed(product_name, error or "提交失败",
                                article_url=product.get("article_url", ""))
        logger.error(f"[通知] {product_name} 延期失败: {error}")

    def notify_waiting(self, product_name: str, reason: str, next_run_ts: int = 0):
        logger.info(f"[通知] {product_name} 跳过: {reason}")

    def notify_error(self, error: str):
        logger.error(f"[通知] 脚本异常: {error}")
        self._sent_action = True
        self.send_review_failed("脚本异常", error)

    # ------------------------------------------------------------------
    # 汇总（保持兼容，主程序结束时调用；若已发过动作邮件则不发检查邮件）
    # ------------------------------------------------------------------
    def send_summary(self) -> bool:
        if not self._sent_action:
            self.send_check()
        return True
