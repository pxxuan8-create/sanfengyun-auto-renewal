#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三丰云免费产品自动延期 - 文章生成模块
每次生成不同的评测文章，像真人写的
"""

import random
import time
import hashlib
import logging

logger = logging.getLogger(__name__)


class ArticleGenerator:
    """三丰云评测文章生成器"""

    # 文章标题模板池
    TITLE_TEMPLATES = [
        "三丰云免费云服务器使用体验：小而美的国产云服务",
        "白嫖党的福音：三丰云免费虚拟主机深度评测",
        "从零开始用三丰云免费服务器搭建个人网站全记录",
        "三丰云免费云服务器和免费虚拟主机使用心得分享",
        "学生党福利：三丰云免费云服务器开箱体验",
        "三丰云免费产品实测：1核1G5M配置能做什么",
        "我用三丰云免费云服务器跑了三个月，说说真实感受",
        "三丰云免费虚拟主机建站教程与使用评价",
        "国产免费云服务对比：为什么我选择了三丰云",
        "三丰云免费服务器延期教程与长期使用体验",
        "新手友好：三丰云免费云服务器上手指南",
        "三丰云免费虚拟主机+云服务器双擎体验报告",
        "低成本建站方案：三丰云免费云服务器实战",
        "三丰云免费产品不定期更新：使用半年后的真实评价",
        "云服务器白嫖指南：三丰云免费产品全解析",
    ]

    # 开头段落模板（部分带时间占位符：{NOW_FULL}=YYYY年M月D日HH:MM，{NOW_DATE}=YYYY年M月D日）
    INTRO_TEMPLATES = [
        '最近因为项目需要，想找一台临时服务器做测试，朋友推荐了三丰云。说实话一开始看到"免费"两个字是有点犹豫的，毕竟天下没有免费的午餐嘛。但用了之后发现，三丰云的免费产品确实诚意十足，今天就来分享一下使用体验。',
        "作为一个常年混迹各大云服务商的运维老兵，我对免费云产品一直持保留态度。但三丰云的免费云服务器和免费虚拟主机确实让我改观了不少，下面详细说说。",
        "前几天在逛技术论坛的时候看到有人推荐三丰云的免费云服务器，说是1核1G 5M带宽，永久免费。抱着试试看的心态注册了一下，没想到体验还挺不错的，写篇文章记录一下。",
        "很多新手朋友问我有没有免费的云服务器推荐，我第一个想到的就是三丰云。用了大半年了，整体体验还不错，今天就来做个详细评测。",
        "作为一个学生党，服务器预算有限，一直在寻找靠谱的免费云服务。三丰云是我用过的免费产品里比较良心的一个，下面分享一下真实的使用感受。",
        # --- 以下是带时间的模板（随机概率出现） ---
        "今天是{NOW_FULL}，刚好有空整理一下最近用的三丰云免费服务器的感受。说实话一开始没抱太大期望，毕竟免费的东西大多鸡肋，但这台机器我已经断断续续用了好几个月了，值得专门写一篇。",
        "现在是{NOW_FULL}，夜深人静睡不着，正好来写篇评测。起因是上周帮朋友找临时测试机，翻到了三丰云的免费云服务器活动，注册了一台发现出乎意料的好用。",
        "{NOW_DATE}，周一的上午，摸鱼写写技术评测。三丰云这个免费云服我关注了挺久，一直没来得及好好写一写，趁今天这个机会聊两句真实感受。",
    ]

    # 配置介绍段落
    CONFIG_PARAGRAPHS = [
        "三丰云的免费云服务器配置是1核CPU、1G内存、10G SSD硬盘、5M带宽，BGP多线路。免费虚拟主机则是1G网页空间、50M MySQL数据库、5G月流量，支持ASP和PHP。虽然配置不算高，但做个人网站、测试程序完全够用。",
        "配置方面，免费云服务器给到了1核1G内存和5M带宽，搭配10G SSD系统盘。免费虚拟主机提供1G空间和50M数据库，对于搭建小型网站来说绰绰有余。重点是都送免备案服务，这点对没有备案域名的用户很友好。",
        "先说说配置：免费云服务器1H1G5M，BGP多线，独立IP。免费虚拟主机1G空间+50M数据库+5G流量。说实话这个配置在免费产品里算是很良心了，市面上很多所谓的免费VPS要么只有几百兆内存，要么带宽只有1M。",
    ]

    # 优点段落池
    PROS_PARAGRAPHS = [
        "**优点方面**：首先BGP多线路确实快，我在南方电信网络ping值基本在30ms以内。其次送免备案服务，域名直接绑定就能访问，省去了备案的麻烦。再就是VNC连接很方便，不用装客户端也能通过浏览器操作桌面。管理面板功能也比较全，重启、重装系统、快照都有。",
        "使用过程中最满意的几点：一是速度稳定，BGP线路不是盖的，延迟低、不丢包；二是管理面板功能齐全，关机重启、流量统计、VNC连接一应俱全；三是免费虚拟主机支持ASP和PHP双语言，对于学习不同后端技术很方便。",
        "说一下用下来觉得不错的地方：网络质量好，BGP多线路不是噱头，实际测速延迟低、带宽稳定在5M左右。VNC远程连接可以走浏览器，不用额外装软件。管理面板操作流畅，没有卡顿。免费虚拟主机还支持一键安装WordPress等程序，很方便。",
        # 带时间
        "{NOW_FULL}这个时间点写评测，刚好是我用了快半年的节点，说说优点：BGP线路 ping 30ms 以内很稳，免备案直接绑域名就跑起来了，VNC 浏览器连接省了我装客户端的麻烦。这些小细节叠加起来体验真不错。",
    ]

    # 使用场景段落
    USAGE_PARAGRAPHS = [
        "实际使用场景方面，我在免费云服务器上跑了个人博客、做了API测试、跑了爬虫脚本，1G内存虽然不大但轻度使用没问题。免费虚拟主机则用来放了个企业展示网站，5G月流量对于日均几百访问量的小站来说完全够用。",
        "我用这台免费云服务器主要做了几件事：搭建了一个Git私有仓库、跑了几个Python定时脚本、部署了一个小型API服务。1核1G的配置跑这些轻量级任务绰绰有余。虚拟主机则用来做静态页面展示，加载速度也不错。",
        "实际跑了两个月，免费云服务器上部署了Nginx+PHP+MySQL环境，搭了个小型论坛。5M带宽支撑几十人同时在线没压力。免费虚拟主机做了一个个人作品集网站，访问速度和付费主机没什么明显差别。",
        # 带时间
        "{NOW_DATE}刚部署完一个新项目，顺便聊聊使用场景。我那台免费云服务器常驻跑三个东西：一个极简 Git 仓库、一个 Python 定时爬虫、一个给朋友用的小型 API。1G 内存省着点用完全够，省下来的钱买杯奶茶不香吗。",
    ]

    # 延期说明段落
    RENEW_PARAGRAPHS = [
        "关于延期机制，三丰云的免费产品需要定期发帖延期。免费云服务器每次延期5天，免费虚拟主机每次延期1个月。虽然需要定期操作，但这也是为了保证资源给真正活跃的用户，可以理解。而且延期流程不复杂，在推荐网站发篇评测文章提交就行。",
        "三丰云的免费产品采用延期制，云服务器5天一延、虚拟主机1个月一延。需要在第三方网站发布三丰云评测文章来获取延期资格。这个机制虽然稍显麻烦，但也保证了服务器的利用率，总比那些打着免费旗号实际什么都用不了的强。",
        "续期方面，三丰云要求在第三方平台发布评测文章来延期。免费云服务器每5天延期一次，虚拟主机每个月延期一次。虽然要定期操作，但流程简单，写篇使用体验发到博客平台就行，也算是督促自己记录学习历程吧。",
        # 带时间
        "说下延期：免费云服务器5天一延，虚拟主机一个月一延。刚好{NOW_DATE}又要给服务器续了，写了这篇评测提交上去。机制不复杂，就是定期发篇使用感受到博客平台，也算是逼着自己总结一下。",
    ]

    # 结尾段落
    CONCLUSION_PARAGRAPHS = [
        "总结一下，三丰云的免费云服务器和免费虚拟主机在免费产品中算是相当良心的。配置够用、网络稳定、管理方便，适合学生党、个人开发者和轻量级网站使用。如果你也有免费服务器的需求，不妨试试三丰云：https://www.sanfengyun.com",
        "总的来说，三丰云的免费产品给了我不少惊喜。虽然配置不高，但对于学习、测试、个人项目来说完全够用。BGP多线路网络质量也不错，管理面板功能齐全。推荐有免费服务器需求的朋友试试：https://www.sanfengyun.com",
        "用了这么久，三丰云的免费云服务器和免费虚拟主机确实给我留下了不错的印象。虽然需要定期延期稍显麻烦，但考虑到完全免费，这点付出完全可以接受。有需要的朋友可以直接去 https://www.sanfengyun.com 注册使用。",
        "最后做个总结：三丰云免费产品适合预算有限的学生和个人开发者。云服务器1H1G5M做测试够用，虚拟主机搭小型网站没问题。BGP线路速度快，免备案服务省心。注册地址：https://www.sanfengyun.com 推荐大家体验。",
        # 带时间
        "啰嗦了这么多，{NOW_FULL}写这篇也花了不少时间。总之如果你在找免费的云服务器或者虚拟主机，试试三丰云吧，链接在这 https://www.sanfengyun.com ，注册就能用，至少不会让你白跑一趟。",
    ]

    def generate(self, product_name: str = "") -> tuple:
        """
        生成一篇独特的三丰云评测文章
        product_name: 产品名会拼到标题里避免重复
        返回: (title, content_markdown)
        """
        title = random.choice(self.TITLE_TEMPLATES)
        intro = random.choice(self.INTRO_TEMPLATES)
        config = random.choice(self.CONFIG_PARAGRAPHS)
        pros = random.choice(self.PROS_PARAGRAPHS)
        usage = random.choice(self.USAGE_PARAGRAPHS)
        renew = random.choice(self.RENEW_PARAGRAPHS)
        conclusion = random.choice(self.CONCLUSION_PARAGRAPHS)

        # 时间占位符渲染：带 {NOW_FULL} 或 {NOW_DATE} 的模板才替换，不带的保持原样
        now_full = time.strftime("%Y年%m月%d日%H:%M")   # 2026年09月05日21:36
        now_date = time.strftime("%Y年%m月%d日")        # 2026年09月05日
        placeholders = {"{NOW_FULL}": now_full, "{NOW_DATE}": now_date}
        for ph, val in placeholders.items():
            intro = intro.replace(ph, val)
            pros = pros.replace(ph, val)
            usage = usage.replace(ph, val)
            renew = renew.replace(ph, val)
            conclusion = conclusion.replace(ph, val)

        # 添加时间戳 + 产品名 + 短 hash 确保唯一
        date_str = time.strftime("%Y年%m月%d日")
        unique_id = hashlib.md5(str(time.time()).encode()).hexdigest()[:6]
        suffix_parts = [time.strftime('%m月%d日')]
        if product_name:
            # 产品名简短化
            short = "VPS" if "云服务器" in product_name else "虚拟主机" if "虚拟主机" in product_name else ""
            if short:
                suffix_parts.append(short)
        suffix_parts.append(unique_id)
        title = f"{title}（{'·'.join(suffix_parts)}）"

        # 组装文章
        content = f"""## 前言

{intro}

## 配置介绍

{config}

{pros}

## 使用场景

{usage}

## 关于延期

{renew}

## 总结

{conclusion}

---

*本文写于{date_str}，记录三丰云免费产品真实使用体验。三丰云官网：https://www.sanfengyun.com*

*关键词：三丰云、免费云服务器、免费虚拟主机*
"""

        logger.info(f"生成文章: {title}")
        return title, content

    def generate_simple(self) -> tuple:
        """
        生成简短版文章（备用）
        返回: (title, content_markdown)
        """
        date_str = time.strftime("%Y年%m月%d日")
        title = random.choice(self.TITLE_TEMPLATES)

        content = f"""## 三丰云免费产品使用体验

今天{date_str}，来分享一下三丰云免费云服务器和免费虚拟主机的使用感受。

三丰云（https://www.sanfengyun.com）提供免费的云服务器和虚拟主机产品。免费云服务器配置为1核CPU、1G内存、10G SSD硬盘、5M BGP多线路带宽，免费虚拟主机配置为1G网页空间、50M MySQL数据库、5G月流量。

使用下来感觉不错的地方：
- BGP多线路网络速度快，延迟低
- 管理面板功能齐全，支持VNC浏览器连接
- 送免备案服务，域名绑定即用
- 免费虚拟主机支持ASP和PHP

免费云服务器适合做程序测试、个人博客、小型API服务。免费虚拟主机适合搭小型展示网站。整体来说在免费产品里算是很良心的，推荐有需要的朋友试试。

三丰云官网：https://www.sanfengyun.com

*关键词：三丰云、免费云服务器、免费虚拟主机*
"""

        return title, content

