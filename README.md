# 三丰云免费产品自动延期脚本（GitHub Actions 版）

> ⚠️ **写在最前——关于三丰云的审核机制**
>
> 三丰云审核只在工作时间进行，大约 3 小时内审完。不上班不审核。
> 如果刚好服务器过期赶上非工作时间，可能**自动删除实例，数据全没**。
>
> 所以这个脚本的意义在于：在到期前尽早提交延期，确保审核在工作时间内完成，避免实例被删。
> 如果到期时间恰好卡在周末或节假日——自求多福。

---

## 它能干嘛？

> 自动扫描 → 博客园发评测文章 → 三丰云填表单提交延期 → **SMTP 邮件通知结果**，全程无人值守，跑在 GitHub Actions 上，**不需要自己租服务器**。

三丰云的免费云服务器（+5天/次）和免费虚拟主机（+30天/次）需要定期发评测文章提交延期。这个脚本把整套流程全自动跑完：

| 步骤 | 做什么 | 怎么判断 |
|------|--------|---------|
| ① 扫描 | 访问三丰云控制面板，从 **DOM 页面文字** 提取到期时间 + 表单状态 | `form_status` 四态：`ready`（有发帖表单可提交）/ `in_review`（审核中）/ `not_yet`（未到时间）/ `not_activated`（未开通） |
| ② 发帖 | 博客园自动生成评测文章并发布 | **只有 `form_status=ready` 才发帖** |
| ③ 提交 | 三丰云后台填入博文 URL + 上传博文截图，提交延期表单 | 提交成功 → 立即发「已提交」邮件（带博文链接） |
| ④ 轮询审核 | 提交后**每 5 分钟重新扫描一次**，直到审核通过或超时 | `form_status` 变为 `not_yet`（页面显示"未到提交时间"）= 审核通过 |
| ⑤ 通知 | 审核通过发「延期成功」、失败/超时发「延期失败」、无动作发「延期检查」 | 标题一眼辨成败：`【三丰云延期成功/失败/检查/已提交】+ 北京时间` |

**每个产品独立判断**，到期的才处理，未到时间的只记录。

---

## 运行机制（两个 GitHub Actions 工作流分工）

| 工作流 | 触发 | 做什么 | 随机时间 |
|--------|------|--------|---------|
| **三丰云自动延期**（`sanfengyun_renew.yml`） | 每 2 天一次 `0 0 */2 * *` + 手动 | 扫描 → 提交延期 → 发「已提交」→ 每 5 分钟轮询审核 → 发「延期成功/失败」 | 定时触发时 0~4 小时随机；**手动触发立即执行** |
| **三丰云每日检测**（`sanfengyun_daily_check.yml`） | 每天一次 `20 0 * * *` + 手动 | 只登录三丰云扫描各产品到期时间 + 状态，发「每日检测」邮件（**不提交任何延期**） | 定时触发时 0~2 小时随机；**手动触发立即执行** |

- **随机时分秒**：GitHub cron 本身不支持随机，两个 workflow 里都加了随机延迟（提交 0~4h、检测 0~2h），**每天/每次实际执行时分秒都不同**，规避"固定时间=定时任务"的人工检测
- **手动触发立即执行**：`if: github.event_name == 'schedule'`——手动点 Run workflow 时跳过随机延迟，直接跑，方便测试（约 2~4 分钟出结果）
- **提交运行完整闭环**：扫描 → 提交 → 发「已提交」邮件 → 每 5 分钟轮询审核（最多约 3 小时）→ 审核通过发「延期成功」/ 失败发「延期失败」
- 原仓库每 60 秒醒一次的常驻 `run_loop` 模式**保留未删**，需要自建服务器时可改用该模式；GitHub Actions 做不到 60 秒级常驻检测，故用"每天检测一次"代替

---

## 目录结构

```
sanfengyun-auto-renewal/
├── .github/workflows/sanfengyun_renew.yml   ⭐ GitHub Actions：每 2 天自动提交延期
├── .github/workflows/sanfengyun_daily_check.yml ⭐ GitHub Actions：每天检测发状态邮件
├── README.md                 📖 本文件（给人看）
├── AI_SPEC.yaml              🤖 机器可读规约（给 AI 看）
├── sanfengyun_renewal.py     ⭐ 主入口（--once 提交 / --status 检测）
├── scanner.py                登录三丰云 + DOM 扫描到期时间 / 延期状态
├── publisher.py              登录博客园 + 自动发文 + 页面截图
├── renewer.py                三丰云后台填写延期表单 + 提交
├── article_generator.py      文章生成器（模板随机拼接 + 时间占位符）
├── email_notify.py           ✅ SMTP 邮件通知（替代原钉钉，分阶段：已提交/成功/失败/检查）
├── config.yaml               🔧 配置文件（占位符，真实值走 GitHub Secrets）
├── requirements.txt          Python 依赖
```

---

## 快速上手（GitHub Actions 部署，推荐）

### 第 1 步：Fork 这个仓库

点右上角 **Fork** 到自己账号下。

### 第 2 步：开启 Actions

仓库 Settings → **Actions → General** → 把 **Allow all actions and reusable workflows** 勾上（Fork 后默认可能禁用）。

### 第 3 步：配置 Secrets（代替 config.yaml 里的真实账号）

仓库 Settings → **Secrets and variables → Actions → New repository secret**，逐个添加：

| Secret 名 | 内容 |
|-----------|------|
| `SANFENGYUN_PHONE` | 三丰云登录手机号 |
| `SANFENGYUN_PASSWORD` | 三丰云登录密码 |
| `CNBLOGS_EMAIL` | 博客园登录邮箱 |
| `CNBLOGS_PASSWORD` | 博客园登录密码 |
| `CNBLOGS_USERNAME` | 博客园用户名（`https://www.cnblogs.com/xxx` 里的 `xxx`） |
| `SANFENGYUN_VPS_URL` | 免费云服务器实例详情页 URL（`.../freeServer/你的实例ID`） |
| `SANFENGYUN_VHOST_URL` | 免费虚拟主机实例详情页 URL（`.../freeVhost/你的实例ID`） |
| `SMTP_HOST` | SMTP 服务器，如 `smtp.qq.com` |
| `SMTP_USER` | SMTP 账号（QQ 邮箱填完整邮箱地址） |
| `SMTP_PASS` | SMTP 授权码（QQ 邮箱是 16 位授权码，不是登录密码） |
| `SMTP_TO` | 收件邮箱，如 `admin@77boss.cn` |

> 🔑 **只有 vps 或只有 vhost？** 没有的那个 URL Secret 可以不填（代码只在 env 有值时覆盖 config.yaml）。也可以在 `config.yaml` 里把对应产品 `enabled: false`。

### 第 4 步：手动触发一次测试

仓库 **Actions** → 选 "三丰云自动延期" → 右侧 **Run workflow** → 绿色按钮。

观察日志：
- `--once` 模式跑完三个阶段（扫描 → 发文 → 填表提交）
- 结束时会打印邮件主题 + 正文（或"未完整配置 SMTP"）
- 你会在 `SMTP_TO` 邮箱收到 `【三丰云延期成功/失败/检查】` 的邮件

> ⚠️ **Actions 上 `settings.dry_run` 恒为 false（真提交）**。想只试不提交，在本地跑 `python sanfengyun_renewal.py --test`（填表不提交）即可。

### 第 5 步：确认定时已开启

仓库 Actions 页能看到 "三丰云自动延期" workflow。**cron 定时任务需要仓库至少每 60 天有一次活动**（提交或手动 run 都算），否则 GitHub 会自动暂停。建议一个月手动跑一次。

---

## 邮件通知格式（免登录确认结果）

每次运行按阶段发邮件，**标题一眼辨成败**，正文干净不含验证过程日志：

**标题**（5 种，按触发时机）：
```
【三丰云延期已提交】2026-09-09 08:23:45   ← 延期申请提交成功后立即发（带博文链接）
【三丰云延期成功】2026-09-09 11:40:12     ← 审核通过（轮询到 form_status=not_yet）
【三丰云延期失败】2026-09-09 11:40:12     ← 提交失败 / 审核失败 / 超时未确认
【三丰云延期检查】2026-09-09 08:23:45     ← 提交运行中本轮无延期操作（未到时间/审核中/未开通）
【三丰云每日检测】2026-09-09 08:23:45     ← 每日检测工作流发（只报状态，不提交）
```

**「延期成功」正文**（审核通过后发）：
```
三丰云免费产品自动延期
==============================
延期审核通过！
确认时间：2026-09-09 11:40:12

[免费云服务器]
  延期状态：成功
  延期天数：5 天
  到期时间：2026-09-12 12:00:00 → 2026-09-17 12:00:00
  博文链接：https://www.cnblogs.com/xxx/p/12345

==============================
(到期时间以三丰云控制台实际显示为准)
```

**「延期已提交」正文**（提交成功后立即发）：
```
三丰云免费产品自动延期
==============================
已提交延期申请，等待三丰云审核
提交时间：2026-09-09 08:23:45

[免费云服务器]
  状态：已提交（待审核）
  延期天数：5 天
  博文链接：https://www.cnblogs.com/xxx/p/12345

==============================
(审核通过后约 5 分钟内会再发一封结果邮件)
```

**「每日检测」正文**（每日检测工作流发，只读状态不提交）：
```
三丰云免费产品状态日报
==============================
检测时间：2026-09-09 08:23:45

[免费云服务器]
  到期时间：2026-09-12 12:00:00
  延期状态：可提交延期

[免费虚拟主机]
  到期时间：2026-09-20 08:00:00
  延期状态：审核中
  可提交时间：2026-09-30 07:58:22

==============================
(仅检测状态，未提交任何延期；提交由每 2 天一次的自动延期工作流负责)
```

> 📌 **延期天数来源**：免费云服务器 +5 天/次、免费虚拟主机 +30 天/次（三丰云官方规则）。到期时间 = 扫描到的当前到期时间 + 延期天数。
> 📌 **审核轮询**：提交后每 5 分钟重新扫描一次页面（默认最多 36 次 ≈ 3 小时），`form_status` 从 `in_review` 变为 `not_yet`（页面显示"未到提交时间"）= 审核通过，发「延期成功」；超时未确认发「延期失败」提示手动查看。
> 📌 **无操作时不轰炸**：本轮没有提交动作时只发一封「延期检查」，过程性通知（扫描、倒计时等）只写日志。

---

## 三种运行模式（本地调试用）

| 命令 | 做什么 | 什么时候用 |
|------|--------|-----------|
| `python sanfengyun_renewal.py --test` | 扫描 → 发文 → **填表不提交**（dry_run=True） | 第一次跑、改配置后本地验证 |
| `python sanfengyun_renewal.py --once` | 扫描 → **可以延期的都完整提交一次** → 每 5 分钟轮询审核 → 发结果邮件 | Actions 提交工作流跑的就是这个 |
| `python sanfengyun_renewal.py --status` | **只扫描**发「每日检测」状态邮件，不发文不提交 | Actions 每日检测工作流跑的就是这个 |
| `python sanfengyun_renewal.py` | 持续循环模式（7×24 常驻，需自己服务器，保留原仓库逻辑） | 不用 Actions 时自建服务器用 |

> 邮件通知在 `--test` / `--once` 结束后按阶段发（test 模式发"检查/失败"类摘要，可用来验证邮件通道）。

---

## 本地运行配置（不依赖 GitHub 时）

用环境变量注入，或直接改 `config.yaml` 填真实值（**注意别提交到公开仓库**）：

```bash
export SANFENGYUN_PHONE=你的手机号
export SANFENGYUN_PASSWORD=你的密码
export CNBLOGS_EMAIL=你的博客园邮箱
export CNBLOGS_PASSWORD=你的密码
export CNBLOGS_USERNAME=你的用户名
export SANFENGYUN_VPS_URL=你的vps实例URL
export SMTP_HOST=smtp.qq.com
export SMTP_USER=你的QQ邮箱
export SMTP_PASS=你的16位授权码
export SMTP_TO=admin@77boss.cn

pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium
python sanfengyun_renewal.py --once
```

Windows 本地跑需要把 `headless=False` 换成能显示浏览器的方式（Windows 有桌面环境，`--once` 直接跑会弹浏览器窗口，正常）。

---

## 配置文件详解（config.yaml）

| 位置 | 默认值 | 说明 |
|------|--------|------|
| `settings.dry_run` | `false` | `true`=只填表单不提交（本地测试用）；`false`=真的提交延期 |
| `settings.sanitize_keywords` | `["三丰云", "三丰"]` | 博客园会拦截"三丰云"，发布后自动替换 |
| `settings.scan_scope` | `"all"` | 扫描范围：`all`=两个都扫；`vps`=只扫云服务器；`vhost`=只扫虚拟主机 |
| `products[*].enabled` | `true` | `false`=跳过这个产品 |
| `logging.level` / `file` | `INFO` / `""` | 日志级别 / 日志文件路径 |

> ⚠️ `check_interval`、`early_submit_seconds` 是遗留字段，当前版本未使用，保留仅为兼容，可忽略。

---

## 常见问题 FAQ

### Q1：邮件没收到？

检查：
1. Actions 日志末尾有没有 `[邮件] 发送成功`？
2. 如果打印 `未完整配置 SMTP`，说明 Secrets 的 `SMTP_HOST/USER/PASS/TO` 没配或没生效（**env 必须与 run 同级缩进**，见 workflow 文件）
3. QQ 邮箱 `SMTP_PASS` 必须是 **16 位授权码**（设置 → 账户 → 开启 SMTP 服务 → 生成授权码），不是登录密码
4. 收件箱 / 垃圾箱都看看

### Q2：博客园发布时说"提交内容中含有不允许的词"？

默认 `sanitize_keywords: ["三丰云", "三丰"]` 会自动替换。还被拦就改成别的替换词：
```yaml
sanitize_keywords:
  - "三丰云"
  - "SFY"
```

### Q3：Actions 报 `error while loading shared libraries`？

workflow 里已带 `playwright install-deps chromium`，不会出现。如果自己本地跑遇到，执行：
```bash
playwright install-deps chromium
```

### Q4：三丰云收到的截图里中文全是 □□□ 方块？

**容器/服务器没装中文字体**。workflow 已带 `fonts-noto-cjk`。本地 Linux 执行：
```bash
sudo apt install -y fonts-noto-cjk fonts-wqy-zenhei fonts-wqy-microhei
fc-cache -fv
```

### Q5：为什么 Actions 里必须 Xvfb？

博客园阿里云验证码会检测 `headless=True`。脚本固定 `headless=False` + Xvfb 虚拟显示。workflow 已装并自动启动 Xvfb（`_ensure_display()` 检测无 DISPLAY 时自动起 :99）。**不要改成 headless=True**。

### Q6：我只有一个产品（比如只有 vps）？

```yaml
products:
  - key: "vps"
    name: "免费云服务器"
    enabled: true
    url: "https://www.sanfengyun.com/control/#/freeServer/你的实例ID"
  # vhost 那段留着 enabled: false 即可
```
对应地，`SANFENGYUN_VHOST_URL` 不填。

### Q7：随机时间会延迟很久吗？

随机延迟 0~4 小时，加在 workflow 里（触发后先 sleep 再执行）。三丰云审核只需工作时间，北京 8:00 触发 + 0~4h 随机 = 8:00~12:00 之间随机开始，都是工作时间，安全。

### Q8：为什么每 2 天而不是每天？

你要求的"每 2 天一次 + 随机时间"，已配置 `cron: 0 0 */2 * *`。要改频率就改 workflow 里的 cron（注意 GitHub cron 的 `*/2` 在月内是 1、3、5…31 这样隔天跑）。

### Q9：`--test` 和 `--once` 啥区别？

- `--test`：填表不提交（dry_run 硬编码），本地验证用
- `--once`：真的提交，Actions 用这个

---

## 安全提醒

### ⚠️ 真实账号一律走 GitHub Secrets，不要写进 config.yaml 提交

- `config.yaml` 已改为**占位符**，可直接提交公开仓库
- 真实手机号/密码/博客园密码/实例 URL 全部放 **Secrets**（见上文"第 3 步"），脚本运行时通过环境变量读取，**不会出现在仓库文件或日志里**
- 邮件正文里不会打印账号密码

### 🔑 SMTP 授权码安全

- QQ 邮箱授权码只在 SMTP 服务器之间传输，Secrets 里加密存储
- 不要在 issue、截图、聊天记录里暴露授权码

---

## 一点说明（为什么不能真的"绕过"三丰云审核）

三丰云的延期审核是**人工审核**，必须在工作时间等结果。这个脚本只能做到"到点自动提交"，没法让非工作时间的审核提前完成。**到期时间卡在周末/节假日时，仍有可能被删实例**——这是平台规则决定的，无解。
