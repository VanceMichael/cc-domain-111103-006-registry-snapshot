# 软件登记源的撤回版本与制品证据

这是一个 Python 数据采集项目，从多个公开包登记源建立**可追溯的版本目录**，供内部审查使用。项目只读取来源材料：不执行依赖安装，不导入或运行包内任何代码，制品文件只按长度和 sha256 摘要校验。所有随附登记记录与制品均为合成数据，不含真实私有包或许可证意见。

## 能力

- **按登记源重放历史观测**：`replay` 以观测时间顺序列出某登记源的全部观测。
- **查找当前状态**：`status` 给出包或单版本的当前视图（可用/撤回/弃用/待复核）。
- **解释状态依据**：`explain` 列出得出当前状态的每份观测及其效果（采用/陈旧/待复核/已隔离/缺席）。
- **隔离与恢复**：审查确认某份来源声明无效后，`quarantine` 保留原观测但让当前视图不再采用；`restore` 或更高修订号的新证据可恢复或了结。
- **幂等采集**：观测标识由内容决定，进程重启与重复获取不会产生多份同一观测。

## 状态判定规则

- 登记记录可声明：新版本(release)、撤回(withdraw)、弃用(deprecate)、重签(resign)、制品替换(replace_artifact)。
- 版本号按数字段比较新旧（1.10.0 比 1.9.0 新），绝不按整串字典序。
- 来源提供的元数据修订号(revision)是同一版本事实的先后依据：修订号更低的晚到观测（如陈旧缓存）不得回滚已应用的状态，因此**撤回早于缓存更新**时撤回生效。
- 同一版本出现不同制品摘要，且无 replace_artifact 声明、也无更高修订号时，视为可信来源互相矛盾：版本进入**待复核(pending_review)**，不按最后到达覆盖。
- 下载地址变化本身不等于制品变化：摘要不变就只更新地址。
- `complete=true` 的批次是来源对某包的完整枚举：已知版本未出现则来源不再提供；`complete=false`（只采到一半）的批次不做任何缺席推断。
- 目录键是 (登记源, 包名)：不同登记源的同名记录指向不同命名空间，绝不跨源合并。
- 元数据修订、制品摘要、签名证明各自保留观测时间与来源（见 `explain` 的 basis 与 attestations）。

## 来源材料

`fixtures/` 下是两个登记源的独立观测快照与场景批次，软件包名称属于各自登记源。`complete=false` 表示来源尚未采全。`fixtures/artifacts/` 是两个合成制品文件，其真实 sha256 与长度被场景批次引用，用于演示缓存核对。版本号、制品摘要及组织均为合成数据。项目按原始来源保存材料；同一来源的版本变化和不同来源的同名记录分别核对。

## 本地运行

项目使用 Python 3.12 或更新版本，仅依赖标准库。从项目根目录执行：

```sh
python3 -m app.inspect
python3 -m unittest discover -s tests -v
python3 -m compileall -q app tests
```

## 命令行

处理结果放在 runtime 目录（可用 `--store` 指定），源文件应保持只读：

```sh
# 采集（可重复执行，幂等）
python3 -m app.cli ingest fixtures/scenarios/r1-batch-01-complete.json

# 重放 / 状态 / 依据
python3 -m app.cli replay --registry registry-one
python3 -m app.cli status --registry registry-one --name synthetic:lib-a
python3 -m app.cli explain --registry registry-one --name synthetic:lib-a --version 2.0.0

# 审查操作（观测标识可只用不重复的前缀）
python3 -m app.cli quarantine <观测标识前缀> --reason "来源声明无效"
python3 -m app.cli restore <观测标识前缀> --reason "新证据恢复"

# 制品核对：只按长度和摘要校验缓存文件是否仍是最新
python3 -m app.cli verify-artifact 缓存文件 --registry registry-one --name synthetic:lib-a --version 1.9.0
```

示例只使用本地虚构资料。若连接真实来源，先按来源方授权范围配置读取权限。
