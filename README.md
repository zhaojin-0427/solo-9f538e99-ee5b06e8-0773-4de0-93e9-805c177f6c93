# XBRL 财报勾稽核查 API（FastAPI + SQLite）

财报入仓员用它判断一份 XBRL 实例与计算关系包之间的勾稽差额，**是否只是
`decimals` 舍入造成的**，从而决定受理或退回重报。

## 核查语义

- 每份实例只允许 **一个实体、一个期间、一种币种、无维度**；数值事实 ≤ 30 个，
  等式 ≤ 30 条，计算权重仅允许 `+1 / -1`，提交时强校验。
- 金额全程按 **原始十进制** 精确保存与计算（`Decimal` / 有理数 `Fraction`），
  不经过二进制 `float`：`9007199254740993`（2⁵³+1）与 `9007199254740992`
  是两个不同的金额，`decimals=0` 在大额下仍保留精确的 ±0.5 闭区间。
- 事实 `decimals=d` 的真值落在闭区间
  `[value − 0.5×10^−d, value + 0.5×10^−d]`；`decimals=INF` 表示精确值（区间退化为点）。
- **全部等式共用同一组事实真值**。核查是一个线性可行性问题，用精确有理数
  两阶段单纯形（`app/exact_lp.py`）求解；判定与见证都不含浮点容差。
- 三种结论：
  - `feasible`：差额可由舍入解释，返回一组金额 **见证**（每个事实的真值、区间、
    报告值）。见证精确落在原始闭区间内且使全部等式残差为 0。
  - `infeasible`：无解，返回一组或多组 **相互矛盾的事实与等式**（最小不可行分组，
    含每条等式在中点见证下的精确残差）。中点见证同样来自原始闭区间。
  - `pending`：任一等式引用了实例中不存在的事实，列出缺失概念，**整份待补**。
- 求解失败（内部错误）时返回 500 `solver_error`，**不落任何报告**；已有报告与
  原始快照保持原样。每次核查都从该修订的原始实例字节重新解析精确金额，
  因此历史修订（即使 JSON 缓存曾用浮点存储）也按原始十进制判断。
- 金额 JSON 表示：整数以 JSON 整数返回（保留全部位数）；无法用二进制浮点
  无损表示的十进制值（如 `9007199254740992.5`）以精确十进制字符串返回；
  非有限小数见证以 `"分子/分母"` 形式返回。核查本身从不依赖这些表示近似。

## 修订与并发（乐观锁）

- 关系包与实例都按修订号（revision）逐版保存，原始 XML 字节永不覆盖。
- 修订关系包需带 `Expected-Revision` 头；更正实例需带 `Expected-Package-Revision` 头；
  核查需同时带 `Expected-Instance-Revision` 与 `Expected-Package-Revision`。
- 受理时校验两侧所见修订：
  - 客户端修订 **领先** 服务器（或修订落后且未声明迟到）→ `412 stale_revision` 拒绝。
  - 更正期间允许继续提交；新报告生成后旧报告标记 `superseded=true`。
  - 对落后修订显式 `POST .../checks?late=true` 时，报告以 `late=true` **仅存历史**，
    不取代当前报告。
- 每份报告随存三份原始依据快照：实例、XSD、calculation linkbase，
  可通过 `/reports/{id}/snapshots/{instance|xsd|calc}` 原样下载。
- 数据全部在 SQLite（WAL 模式）+ 命名卷 `/data` 中，**重启后报告与快照仍可查**。

## 启动（Docker Compose）

```bash
docker compose up --build
# API:   http://localhost:8000
# 文档:  http://localhost:8000/docs   （可直接在 Swagger UI 更正、核查、读取报告）
```

首启会自动注入演示关系包与 4 个场景实例（feasible / rounding / infeasible / pending）。
设置 `XRECON_SEED_SAMPLES=0` 可关闭。

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/packages` | 上传 XSD + calculation linkbase，创建关系包（rev 1） |
| POST | `/packages/{id}/revisions` | 修订关系包（头 `Expected-Revision`） |
| GET | `/packages`、`/packages/{id}` | 列表/详情（含解析后的等式） |
| POST | `/packages/{id}/instances` | 上传 XBRL 实例（rev 1） |
| POST | `/instances/{id}/revisions` | 更正实例（头 `Expected-Package-Revision`） |
| GET | `/instances`、`/instances/{id}` | 列表/详情（含事实与舍入区间） |
| POST | `/instances/{id}/checks` | 核查并出报告（两个 Expected 头；`?late=true` 迟到补报） |
| GET | `/instances/{id}/reports` | 当前报告 + 全部历史（含过期、迟到） |
| GET | `/reports/{id}` | 单份报告 |
| GET | `/reports/{id}/snapshots/{kind}` | 下载报告保存的原始依据 |

## 本地开发与自测

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest httpx
python -m pytest -q
```

## 目录

```
app/            FastAPI 应用、解析器、精确有理数求解器、SQLite 持久化
tests/          37 个端到端/单元测试 + 样本 XBRL
scripts/        容器首启演示数据播种
Dockerfile, docker-compose.yml
```
