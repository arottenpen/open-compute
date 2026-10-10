# P23：Cloudflare Containers 兼容设计

状态：planned。cf 配置与命令入口已核对，两阶段 provider 路线已设计；待 CT0 合同冻结、workerd 动态 Container G0、短期 Docker Broker、长期嵌入式
runtime G0、实施与验收。

本文细化 [P6 Cloudflare v4 API 管理面](implemented/p6-cloudflare-v4-wrangler-compatibility.md)
中的 Container upload、Containers control API 和 Durable Object Container runtime，应用入口遵循
[P20 Cloudflare CLI 单轨迁移](implemented/p20-cf-cli-migration.md)。P23 以固定 Cloudflare 公共合同、
`@cloudflare/containers`、cf/官方 Vite 插件与固定 Miniflare source snapshot 与已授权的 `third_party/workerd/` fork 为依据，不把
Miniflare 的开发期 Docker socket 当作生产安全边界，也不宣称复制 Cloudflare 全球 Container fleet。

## 1. 范围与结论

P23 Day 1 目标：

- `cloudflare.config.ts` 的顶层 `containers[]`、Worker 的 `exports.durableObject({ storage: "sqlite", container })` 与 `bindings.durableObject()`；
- cf/Build Output multipart Worker metadata、Container application、image 与 rollout 调用序列；
- 固定 Workers types 中公开的 Durable Object `ctx.container`；
- 固定 `@cloudflare/containers` 的 Container class、routing、readiness、alarm、HTTP/WebSocket 与 lifecycle 行为；
- `start`、`exec`、`destroy`、`signal`、`monitor`、`getTcpPort` 和声明支持的 outbound interception；
- 短期依赖宿主机 Docker、通过 operator-owned Broker 执行；长期用可嵌入 runtime 与 Docker 子集 shim 替换该外部依赖；
- 单机部署适用的 image admission、capacity、instance inventory 和 rollout；
- 固定 cf `containers` commands 中通过 CT0 inventory 选定的标准子集。

结论：**Worker 侧公开 API 可以以固定版本为边界做到兼容；Cloudflare 全球基础设施语义不能、也不应伪装为完全等价。**
实现必须保留 workerd 原生 `ctx.container`，让固定官方 package 直接运行：

```text
tenant Worker / @cloudflare/containers
  -> native ctx.container
  -> workerd Container implementation
  -> private pinned Docker-subset endpoint over Unix socket
     -> short term: restricted Broker -> operator-installed host Docker
     -> long term: embedded shim -> BoxLite or another qualified embeddable runtime
  -> one application container + one egress interceptor sidecar per running identity
```

P23 与 [P22 Browser Run](implemented/p22-browser-run.md) 共享 operator/runtime 与平台 authority 的 ownership 原则；
P22 的浏览器由 operator 安装或通过 CDP 接入，P23 的外部 Docker／长期嵌入式 runtime 分发策略由本方案单独定义：

1. `ocd` 仍是唯一公开产品 API 入口和 account/deployment/image/capacity authority；provider 网络的可达范围见第 12 节；
2. 短期 Docker 与 Broker 由 operator 预先部署，open-compute 不下载、不搜索 `PATH`、不启动、不 supervise 两者；
3. 长期 runtime 以 library/native dependency 嵌入 `ocd`，由 `ocd` ownership 管理；候选 G0 通过前不固定为 BoxLite；
4. 正式 release 目标仍是单个 `ocd` executable 与既有单个 workerd child；嵌入式 microVM runtime 可以创建受监督的 VMM/helper
   child，不把“单 executable”误写成“单 OS process”；
5. engine socket、runtime credential、provider container ID 与 host topology 永不暴露给 tenant；
6. 当前阶段 provider 未配置、合同不匹配或资源不足时 upload/readiness/start fail closed；
7. tenant 不能指定 engine endpoint、host mount/device、privileged mode、runtime socket 或 provider credential。

Containers 与 Browser Run 的关键差异是：Browser binding 可以落为 HTTP/CDP provider protocol；Container 是绑定在 DO
context 上的 native capability，包含 Fetcher、Socket、streaming exec、monitor 和 lifecycle identity。P23 不用普通 HTTP
service binding 重写这套 API。

## 2. Compatibility authority

实施和 qualification 固定：

- [Cloudflare Containers](https://developers.cloudflare.com/containers/)；
- [Durable Object Container API](https://developers.cloudflare.com/durable-objects/api/container/)；
- [Container lifecycle](https://developers.cloudflare.com/containers/concepts/architecture/)；
- [Container local development](https://developers.cloudflare.com/containers/guides/local-dev/)；
- [Container rollouts](https://developers.cloudflare.com/containers/configuration/rollouts/)；
- [Container limits and instance types](https://developers.cloudflare.com/containers/platform/limits/)；
- [Cloudflare CLI source](https://github.com/cloudflare/cf) 的 Containers commands、schema 与 deploy producer；
- [Cloudflare `@cloudflare/containers`](https://github.com/cloudflare/containers)；
- [workerd Container configuration/source](https://github.com/cloudflare/workerd/blob/main/src/workerd/server/workerd.capnp)；
- 当前 CLI 基线 `cf@1.0.0-beta.12`、`cf/config`（`@cloudflare/config@0.23.0`）、官方 Vite 插件 v2 与 Build Output；
- 早期研究使用 Miniflare `5.20260828.0-alpha` / workers-sdk `f8085545bcaa2c639f171c25e4424685036a0e10`；
  仅保留为 runtime 行为调查来源，CT0 重新固定当前开发工具依赖及调用链，不能作为当前 cf 验收证据；
- 当前研究候选 `@cloudflare/containers@0.3.7`；CT0 必须把最终 package tarball、integrity、types 和 source revision 固定；
- 当前 workerd source/gitlink 与正式 binary 分别由 [workerd runtime policy](workerd/README.md) 和
  `packages/runtime/workerd.lock.json` 固定；CT-G0 记录实际输入，源码 checkout 不代替正式 pin；
- 固定 Cloudflare remote traces、OpenAPI revision/hash、Workers types 与 cf subprocess fixtures。

网页和 upstream source 用于发现合同；进入 Gate 的 property descriptor、method signature、同步 throw、promise rejection、
stream/Socket 行为、HTTP/WebSocket、signal/exit、restart 和 CLI/API wire shape 必须固定为 inventory/fixture。Cloudflare
Containers 仍在快速演进，未进入 inventory 的 beta、experimental 或新字段默认 unsupported。

不得因为当前 fork 暴露了比官方文档更多的 method/options 就自动扩大兼容范围。workerd-only experimental capability 必须由
兼容日期/flag 隐藏或明确拒绝，除非 CT0 把它登记为正式支持的 Cloudflare 合同。

## 3. 兼容声明边界

P23 把“兼容”拆成三个可验收层次：

| 层               | 目标                                                      | 声明                              |
| ---------------- | --------------------------------------------------------- | --------------------------------- |
| Worker API       | 固定 `ctx.container`、Workers types、官方 package         | 目标为逐 API 行为兼容             |
| cf/control plane | 固定 config、upload、application/image/rollout/commands   | 只声明 inventory 中通过的标准子集 |
| Cloudflare fleet | 全球 placement、预热、跨机房路由、Cloudflare VM/配额/计费 | 明确不实现，不宣称等价            |

正式 capability 文案：

> open-compute supports the documented Cloudflare Worker Containers API for the pinned compatibility date, workerd
> revision, Workers types, cf revision, and `@cloudflare/containers` package. Placement and execution use a
> single-machine operator-owned provider and do not emulate Cloudflare's global scheduling infrastructure.

允许的单机偏差：

- placement 固定为当前 open-compute host，不接受或伪装 `Region:Earth`；
- DO 与其 Container 在同一台机器执行，但仍通过 native capability 通信，不泄露 localhost endpoint；
- 不承诺 Cloudflare image prefetch location、1–3 秒 cold-start、fleet autoscaling 或跨机迁移；
- instance type 映射到 operator 配置的本机资源上限，不表示 Cloudflare plan entitlement；
- rollout 保持公开状态机和可观察顺序，但只调度本机实例；
- runtime isolation 由声明的 host Docker 或 embedded provider profile 实现，不把普通容器称为 Cloudflare per-instance VM；
- 产品面向 operator 管理的单机 SMB 部署；不承诺阻止可达宿主/provider 网络的调用方绕过 Worker 直接访问 Container，
  不为该场景增加 sidecar/relay 实例鉴权或跨 Container 网络隔离。API/capability 授权与 engine socket 保护仍须成立。

这些偏差必须进入 `references/cloudflare-compatibility.md`、capability manifest、用户文档和 differential report；不能只埋在
P23 中。

## 4. 五层合同，不混为一个 API

| 层                    | 调用方              | 合同                                                | authority                                        |
| --------------------- | ------------------- | --------------------------------------------------- | ------------------------------------------------ |
| cf config/upload      | cf / 官方 Vite 插件 | TypeScript config、Build Output、multipart metadata | 固定 cf/config/source/wire fixture               |
| Containers public API | cf、SDK、operator   | `/client/v4/accounts/{account_id}/containers/**`    | `ocd`                                            |
| Worker high-level API | tenant package      | `@cloudflare/containers` classes/helpers            | 固定 package                                     |
| Worker low-level API  | tenant DO           | native `ctx.container`、Fetcher/Socket/streams      | workerd                                          |
| Engine provider       | workerd/`ocd`       | pinned private Docker-subset contract               | 短期 external Broker；长期 embedded shim/runtime |

Worker upload 成功不代表 image materialization 或 rollout 已完成。CT0 必须从当前 cf deploy producer 与真实 trace
冻结 Worker upload/activation、image build/push、application update 和 rollout 的顺序、错误及成功退出含义；
旧客户端的调用顺序不作为当前 cf 合同。各步不是跨资源事务，未 materialize 的镜像不得在 runtime 被静默 pull。

## 5. cf 配置、Build Output 与 upload contract

### 5.1 标准配置

```ts
import {
  bindings,
  defineConfig,
  defineContainer,
  defineWorker,
  exports,
} from "cf/config";

const apiContainer = defineContainer({
  name: "api-container",
  image: { dockerfile: "./Dockerfile" },
  maxInstances: 4,
  instanceType: "lite",
  rollout: { stepPercentage: [10, 100] },
});

const worker = defineWorker({
  name: "container-app",
  entrypoint: "./src/index.ts",
  compatibilityDate: "2026-09-07",
  workersDev: false,
  exports: {
    ApiContainer: exports.durableObject({
      storage: "sqlite",
      container: apiContainer,
    }),
  },
  env: {
    API_CONTAINER: bindings.durableObject({
      worker: "container-app",
      exportName: "ApiContainer",
    }),
  },
});

export default defineConfig({ worker, containers: [apiContainer] });
```

此例展示固定 `cf/config` 的声明关系，不表示 P23 已可部署。mode 求值、DO export lifecycle 与 Build Output
由 cf 和官方构建链拥有；`ocd` 验证实际上传合同，不读取 TypeScript 项目文件或沿用旧 migrations/tag 配置。

字段、默认值、互斥关系、mode 求值和错误文本以固定 cf/config schema、deploy producer 与 tests 为准。
配置使用 camelCase；v4 multipart/API wire 按各自 schema 冻结，不把配置字段拼写直接当成 wire。特别要求：

- `exports` 中的 Container DO 必须是同一 Worker 的 SQLite-backed export，env binding 指向该 export；
  wire 中的 `class_name`/export 关联由 CT0 fixture 固定，不能指向另一个 script；
- application name、class name、binding name 与 account/script 唯一性逐项按 fixture 校验；
- `instanceType`（wire `instance_type`）的 named/custom 两种形状不能混用，resource 值在 admission 时归一化一次；
- `maxInstances`、`rollout` steps/grace、scheduling/affinity 中未实现的字段明确拒绝，不能静默忽略；
- `image` 是开发/部署输入，不作为 tenant runtime 可修改字段；
- tenant config 不接受 engine socket、Docker host、privileges、devices、mounts、network mode 或 provider auth；
- P23 只实现当前 cf/Build Output 的 Day1 schema；旧项目由用户显式 `cf migrate <exact-file> --bundler vite`，
  不在平台内读取旧配置或增加兼容路径。

### 5.2 Worker multipart metadata

CT0 从固定 cf/Build Output upload tests 与真实 multipart 冻结 metadata；以下是需核对的关联，不继承旧 producer 的已验证结论：

- metadata `containers` 关联 application name 与 DO `class_name`；
- Worker export 标记相应 DO class 的 Container link；
- immutable Version 保存 Container descriptor digest，不保存本地 Dockerfile path、engine endpoint 或 credential；
- deployment active pointer 与 application target/rollout 分开，保留 Cloudflare 非事务顺序。

当前 service 在 [DO lifecycle validator](../crates/service/src/workers_http/v4/do_lifecycle.rs) 明确拒绝 `container` export。
P23 实施时直接修改当前 Day 1 validator/schema/fixtures，不保留“旧版本拒绝、新版本接受”的历史分支。

### 5.3 Build 与 image push

镜像 build 属于 developer/CI tooling：

- 本地 Dockerfile 由固定 cf 部署/构建链与 developer/CI-installed Docker-compatible CLI build；
- production `ocd` startup、Worker request 和 Container `start()` 都不得运行 Dockerfile build 或访问公网 registry；
- 标准 push/import route 接收 OCI manifest/config/layers，按 digest 校验后写入平台 artifact authority；
- image tag 只在 deployment admission 时解析一次为 immutable digest；runtime 不重新解析 `latest`；
- private registry credential 只由 developer CLI/provider secret reference 使用，不进入 Worker metadata、SQLite plaintext、logs 或
  Broker labels；
- remote registry ref 若未通过 CT0 和安全 import flow，部署明确 unsupported，不能由 Broker 在首次请求时偷偷 pull；
- image 缺失、digest mismatch、architecture 不支持或 provider materialization 失败时 rollout/start fail closed。

## 6. Worker `ctx.container` contract

### 6.1 Public inventory

CT0 从固定 docs、Workers types、workerd JSG declarations 和 package call graph 生成唯一 inventory。Day 1 至少覆盖：

```text
ctx.container.running
ctx.container.start(options?)
ctx.container.exec(command, options?) -> ExecProcess
ctx.container.destroy(reason?)
ctx.container.signal(signo)
ctx.container.monitor()
ctx.container.getTcpPort(port) -> Fetcher with fetch()/connect()
ctx.container.interceptOutboundHttp(...)
ctx.container.interceptOutboundHttps(...)
ctx.container.interceptOutboundTcp(...)  # only when public for the pinned contract
```

`ExecProcess` inventory 包含固定版本公开的：

```text
pid, stdin, stdout, stderr, exitCode
output(), kill(signal?), resize(...)
```

必须逐项固定：

- property 在 prototype/instance 上的位置、enumerability 和 `undefined`/`null`；
- `start()` 的同步返回与后台启动语义；
- already-running/not-running、空 command、非法 signal/options 的 exception class/message；
- stdin EOF、stdout/stderr pipe/ignore/combined、PTY、AbortSignal 和 backpressure；
- `output()` 的单次消费规则与 stream 已消费后的 `TypeError`；
- `monitor()` resolve/reject、destroy reason、restart generation 与旧 monitor fencing；
- Fetcher HTTP body/WebSocket upgrade、Socket half-close、container exit/disconnect race；
- compatibility date/flag 对 HTTPS/TCP interception、PID namespace 和其他新能力的影响。

workerd 当前还含 images、inspect、labels、snapshots、restore 与其他 experimental surface。它们只有同时出现在固定官方
docs/types/package contract 并完成 provider、安全、restart 与 differential coverage 后才可加入 supported inventory。

### 6.2 官方 package

固定 `@cloudflare/containers` 必须不经 fork、不经 import rewrite 直接运行，至少覆盖：

- `Container` subclass construction 与 `ctx.container` presence check；
- `defaultPort`、`sleepAfter`、`envVars`、`entrypoint`、`enableInternet`；
- `start`/`stop`/`destroy`、readiness retry 和 activity renewal；
- `onStart`、`onStop`、`onError`、alarm/schedule；
- `fetch()` HTTP/WebSocket proxy；
- `getContainer()` 的 DO identity/routing；
- 声明支持的 outbound handlers/interception；
- DO eviction、workerd restart、Broker/runtime restart 后的状态恢复。

只让一个 demo request 成功不构成 package compatibility。package 的 storage/alarm state 与 Container process state 必须在
restart 和 rollout 中保持一致的可观察行为。

## 7. DoHost 与 workerd 原生集成

### 7.1 当前结构阻点

open-compute 的 tenant DO 通过 [DoHost](../packages/runtime/src/durable-objects/host.ts) 和动态
`WorkerLoader::WorkerCode` 装载。当前 [WorkerLoader descriptor](../third_party/workerd/src/workerd/api/worker-loader.h) 没有
Container namespace/image/policy；workerd 则把 Container `imageName` 配在静态 DO namespace 上。这导致：

- 所有 tenant class 共用一个静态 `DoHost`，不能用一个 `imageName` 表示不同 account/script/version/class；
- 仅给 `DoHost` 静态开启 Container 会把 capability 扩散给未声明 Container 的 tenant；
- tenant deployment image 更新不能自然参与现有 route generation/version fencing；
- TypeScript facade 无法安全复制 native Fetcher/Socket/stream/monitor brand 与 lifetime。

### 7.2 Day 1 workerd extension

在已授权 fork 中增加一个聚焦的动态 Container attachment，不建立第二套 runtime：

```text
trusted DoHost loads immutable Worker snapshot
  -> WorkerLoader receives optional validated ContainerClassPolicy
  -> trusted native owner resolves the stable DO's current instance lease
  -> loaded DO class/facet gets a revocable native rpc::Container capability
  -> ctx.container exists only for that declared class
  -> native workerd Container API talks to configured Broker
```

`ContainerClassPolicy` 是系统 Worker 与 workerd 之间的私有 structured value，至少包含：

```text
application_resource_id
deployment/version/class descriptor digest
declared Container capability and supported API policy
```

规则：

- class policy 只由 `ocd` 持久 authority 生成的 authenticated/immutable runtime snapshot 派生，证明该 class 与 application 的关联；
- 运行状态另由 authenticated authority 查询返回：DO identity、当前 route generation、有效 instance lease，以及启动时选定的
  immutable image/provider reference、normalized resources 和 egress policy；不把可变 lease/rollout 状态固化进 Worker snapshot；
- tenant module、env、request、DO RPC props 不能构造或覆盖 policy；
- 未声明 Container 的 class 得到与 Cloudflare 一致的 absent/unsupported surface，不共享 `DoHost` 的 capability；
- 接管已有实例验证其有效 lease，允许尚未被 rollout 替换的旧 effective target；新建实例由 `ocd` 按当前 application/rollout
  规则选择已准入 target 并分配 lease，native `start()` 只能使用该 lease 的 immutable image，不能通过 experimental `image` 绕过；
- workerd 给 Broker 的 create request 带 platform-owned opaque labels 与资源字段，不带 account name、secret 或 source path；
- route generation 撤销旧 Worker/facet 的调用权限，object generation 隔离删除重建的对象，lease generation 隔离每次运行；
  application target 前进本身不撤销仍有效的旧实例 lease；
- superseded helper、静态占位 `imageName` 和 JS membrane 在正式实现中删除。

G0 可以用一个最小 JS membrane 验证 official package call graph，但不得把它作为最终 security/compatibility boundary。若 native
brand、facet context 或 lifecycle 无法保持，G0 直接转为上述 fork extension，不堆叠 wrapper workaround。

### 7.3 Container identity

复用已有 DO authority 和计划中的 application/lease 字段，分别表达四种身份：

| 身份 | 含义 | 变化时机 |
| ---- | ---- | -------- |
| DO identity | instance scope、namespace resource ID、object ID、object generation | 对象删除重建等身份变化 |
| Worker route generation | 当前获准执行该 DO 的 Worker 版本 | Worker 发布或回滚 |
| application target generation | application 期望使用的 Container 配置 | Container 配置发布或回滚 |
| instance lease generation | 一次具体的 Container 运行 | 每次重新启动，包括同一镜像的重新启动 |

一个 running Container 对应一个明确的 DO identity 和 instance lease：

```text
instance / namespace-resource-id / object-id / object-generation
  + application-resource-id / lease-generation
  -> opaque engine instance key
```

engine name 使用 keyed digest/opaque ID，不把 account、script、class、DO ID 或 deployment name 暴露给 Docker CLI、provider logs
或其他 tenant。相同 DO identity 同时最多一个 application container，包括状态尚未确认终止的 `lost` lease。lease 的 target/image
记录本次运行的 immutable effective configuration，不要求等于 application 最新 target。过期调用权限、不匹配 DO identity、
已终止 lease 或不匹配 start identity 必须拒绝，不能连接到“名字碰巧相同”的进程。

### 7.4 Worker/facet 更新时的原生交接

Container attachment 由可信 DoHost 对应的 native owner 持有，绑定稳定 DO identity；tenant facet 只持可撤销控制权限。
复用 workerd 的 `ContainerClient` 引用计数、client 查找和 cleanup generation，不另建 JS Container 实现或第二套 process authority。
[现有 actor lifecycle](../third_party/workerd/src/workerd/server/server.c++) 释放引用后，inactivity timer 可能继续持有 client；
发布交接必须显式保留引用，不能依赖 timer 恰好存在。

在现有 DO 串行执行边界内完成交接：

1. 验证新的 route authority，并保留当前 attachment/client 引用；
2. 撤销旧 facet 控制权限，再 abort 旧 facet、加载新 class；
3. 验证新 class 仍声明同一 application 的 Container capability；
4. 新 facet 通过原生 `ctx.container` 接管现有有效 lease，随后释放交接临时引用。

交接不调用 Docker stop/delete/create，不改变 lease、镜像、进程或临时 filesystem。Worker code-only deploy 不重启 Container。
删除或改绑 Container class 不走接管路径，按显式 application/object lifecycle 停止旧实例；加载失败保留现有 lease 与容量占用，
返回稳定失败并允许后续可信重试，不把接管失败当成容器退出。owner/临时引用由恢复或明确终止路径释放，不永久保活。
普通 DO eviction、inactivity 和平台 shutdown 的停止规则仍由固定合同决定；发布交接不改写这些规则。

所有异步 engine mutation、exec、monitor、port handle 与 cleanup 都绑定具体 lease/start identity。旧 facet 不得再发起操作，
撤销前已获准的操作仍只作用于原 lease；延迟 stop/delete 不能按 DO 名字命中新 lease。旧 Promise 不迁移到新 isolate，
新 facet 重新观察当前 lease；outbound interception 不得继续调用已撤销的 facet，由固定合同清理或重新绑定。
保持容器运行不等于保证旧 Worker 的 HTTP/WebSocket/exec stream 无中断；CT0 分别冻结断开、取消与重新观察的行为。

## 8. 两阶段 Container Engine provider

### 8.1 固定决策

P23 只保留两条有顺序的实现路线，不再把 Podman、containerd、crun/runc 或直接修改 workerd provider API 列为并行产品方案：

| 阶段 | workerd 下游                            | engine                                                                            | 目的                                               |
| ---- | --------------------------------------- | --------------------------------------------------------------------------------- | -------------------------------------------------- |
| 短期 | restricted external Broker              | operator-installed host Docker                                                    | 最小工程量取得真实兼容、生命周期与安全证据         |
| 长期 | `ocd`-owned embedded Docker-subset shim | [BoxLite](https://github.com/boxlite-ai/boxlite) 或另一个通过 G0 的可嵌入 runtime | 移除宿主 Docker/Broker 部署依赖，保持 workerd 不变 |

两阶段复用同一个按 workerd revision 固定的私有 wire contract、policy validator、错误映射和测试矩阵。长期阶段替换 engine backend，
不新增第二套 Worker API、JS membrane 或 workerd native provider。BoxLite 是当前首选研究对象，不是已冻结依赖；最终候选必须证明
OCI image、app/sidecar 网络等价、exec streaming、archive/snapshot、资源 enforcement、crash recovery、目标平台和 release linking。

### 8.2 为什么短期需要 Broker

workerd 当前 `containerEngine.localDocker` 直接连接 Docker socket，并标明只用于 local development/testing。生产不能把真实
Docker socket直接交给 workerd Container path，因为：

- Docker API 权限接近 host root；
- workerd config 的 capabilities/devices/security options 会原样传给 Docker，当前无 allowlist；
- local implementation 不是 open-compute 的 account/application capacity authority；
- tenant-visible option 和 engine extension 演进可能扩大可创建对象；
- raw Docker errors、IDs、paths 和 daemon topology 不得进入 Worker。

Broker 是一个受限 Docker Engine protocol endpoint，不是新的 public Container API。它校验并转发 workerd 实际调用的最小 HTTP
subset 到宿主 Docker，使 upstream Container lifecycle/exec/network/recovery code保持唯一实现。生产不提供绕过 Broker 的 direct
Docker 模式；direct socket 只允许 disposable G0/local development，并且不进入 capability 声明。

### 8.3 短期 Docker + Broker 部署合同

- Broker 只监听绝对路径 Unix domain socket；不支持 tenant 配置 TCP endpoint；
- socket parent、owner、mode、symlink/path containment 与 peer credential 在 `ocd`/workerd startup 前验证；
- operator 在启动 `ocd` 前提供 Broker 与 Docker Engine；应用和固定 digest 的 `proxy-everything` image 必须预先 materialize；
- `ocd` 不启动、停止或 supervise Broker，但 readiness 检查 capability/version；
- Broker contract version、runtime kind、isolation profile、supported architecture/features 形成 secret-free digest；
- contract 变化后旧实例不透明认领为新合同；按 reconciliation 关闭或标 lost；
- Broker 不可用时 workerd/container readiness degraded，非 Container Workers 可继续按现有平台 admission 运行；
- 正式 binary 不打包 Docker、containerd、runc、CNI、BuildKit、镜像或 `proxy-everything`。

当 `ocd` 自身运行在 Docker 中时，宿主 `/var/run/docker.sock` 只 bind mount 给 Broker；这是 Docker-outside-of-Docker，Broker
创建的是宿主 sibling containers。Unix socket 的 `:ro` mount 不能限制 API 写操作，容器用户还必须匹配宿主 socket 的数字
GID。workerd 只看到共享 volume 中的 Broker socket，不能看到真实 Docker socket。

当前 workerd 会读取 sidecar published `HostPort`，再硬编码访问 `127.0.0.1:<HostPort>`。普通 bridge 模式中的容器 loopback
不是 Docker host loopback，因此仅挂载 socket 不足以工作。生产部署要求 Broker 与 `ocd`/workerd 共享 network namespace，并由
Broker 在该 namespace 建立 loopback relay；Linux `network_mode: host` 仅作为 G0/development 简化方案，不作为默认生产合同。
此 relay 解决连接拓扑，不作为 tenant 间的访问隔离边界。当前 workerd 使用 `PublishAllPorts`，实际发布地址由 Docker/provider
配置决定；operator 管理其可达范围。P23 不增加防止直连 Container 的发布端口隔离、relay 实例鉴权或绕过入口攻击 Gate，
也不据此宣称只有 DO 可以在网络层访问 Container；此范围不放宽 Broker/Docker 管理接口的授权与 socket 保护。

### 8.4 长期 embedded runtime + Docker 子集 shim

长期在 `ocd` 中嵌入 BoxLite 或另一个通过资格验证的 runtime，并让同一私有 endpoint 从“校验后转发 Docker”变为“把固定
Docker 子集翻译为 native runtime operations”。provider 替换本身不要求修改 workerd Cap'n Proto 或调用方。`ocd` 必须拥有 runtime、
VMM/helper child、socket、readiness、bounded logs、restart/reap 与离线物化；不引入另一个 operator-managed daemon。

最大的 G0 是 workerd 的 application container 与 `proxy-everything` sidecar 共享 network namespace语义。候选 runtime必须证明
可以原生表达，或由 shim 提供等价 sidecar control/loopback relay/egress mediation；不能只伪造 inspect response。snapshot API
还必须证明 Docker commit/image ancestry/named-volume 可观察语义可以安全映射。基础 13 个 operation family 先实现而 snapshot 8 个
family 后实现时，必须公开声明 snapshot API unsupported，不能宣称完整 Worker Container API兼容。

### 8.5 最小 engine operation inventory

C-G0 从当前 workerd `ContainerClient` trace 冻结最小集合：

```text
container create/start/stop/kill/wait/inspect/delete/archive
exec create/start/resize/inspect
image inspect/delete/commit where required by supported snapshots
bridge/network inspection
volume create/inspect/delete where required by supported snapshots
application/egress-sidecar status and reconnect
```

当前 pin 的基础集合是 13 个 family：`GET /networks/bridge`；container create/inspect/start/stop/kill/wait/delete；exec
create/start/resize/inspect；archive PUT。完整 snapshot 再增加 archive GET、volume create/list/inspect/delete、image
commit/inspect/delete，共 21 个 family。query variant、request/response field、status code、exec HTTP upgrade与8-byte multiplex
framing、tar path和wait/remove race都是合同的一部分，不能只按 route name判断兼容。

任何未登记 Docker API route、query、HostConfig、mount、device、capability、namespace 或 registry credential 都由 Broker 拒绝。
不做通用 Docker remote proxy，也不提供 operator shell passthrough。

### 8.6 双重 enforcement

workerd 在 native authority boundary 校验 Worker-visible参数；Broker 在 host security boundary 再校验 effective spec：

- image 必须是已准入 immutable digest；
- CPU、memory、disk、PID、open files、process count 和 instance count 不超过 application/operator limit；
- rootless/user namespace/seccomp/AppArmor 等由 provider profile声明并可验证；
- 禁止 privileged、host PID/network/IPC、host device、Docker socket 和任意 host bind mount；
- 只允许 platform-owned ephemeral volumes/mounts；
- container name、labels、network 和 cleanup token 由平台生成；
- log/output size、exec count、exec stream 和 concurrent port connections bounded；
- unsupported architecture、FUSE/device/capability 明确拒绝，不模仿 Miniflare 自动提权。

信任 workerd 是唯一合法 socket caller不等于信任每个 create payload。Broker 应核对 Unix peer identity、固定 workerd-generated
labels/spec schema 和 operator policy；在单机边界足够时不新增分布式签名/token 服务。

## 9. Image authority 与 supply chain

建议当前 Day 1 schema 直接包含：

```text
container_images
  resource_id, account_id, manifest_digest, config_digest,
  platform, architecture, compressed_bytes, unpacked_bytes,
  artifact_key, admission_state, created_at

container_applications
  resource_id, account_id, script_id, class_name, name,
  max_instances, cpu_limit, memory_limit, disk_limit, pid_limit,
  egress_policy, target_generation, effective_generation, state

container_application_targets
  application_id, generation, worker_version_id, image_digest,
  provider_contract_sha256, rollout_kind, rollout_steps,
  active_grace_period, created_at
```

这是 Day 1 current schema，不增加旧 schema backfill/dual read。SQL sequence、checksums、fixtures 和 dispatch 同步修改。

image invariant：

- OCI manifest/config/layer digest 全链校验；
- platform/architecture、size、entrypoint、declared ports 与不安全 metadata 在 admission 时验证；
- artifacts immutable/content-addressed，tag 只是 control-plane pointer；
- Broker materialization 从平台已验证 artifact/provider import contract取得内容；
- engine cache 是 disposable acceleration，不是 image authority；
- cache hit 仍验证 digest；cache corruption 删除该 cache entry 并重新 materialize，不修改 authority；
- runtime 未准备好目标 digest 时 `start()` 返回稳定 unavailable，而不是运行旧 tag 或下载公网镜像；
- rollout/rollback只切 target/effective pointer，不修改已存在 image bytes。

## 10. Instance lifecycle、recovery 与 ownership

职责保持单一：

| concern                                               | owner                                |
| ----------------------------------------------------- | ------------------------------------ |
| application/image/target/rollout/capacity authority   | `ocd` + SQLite/artifact store        |
| Worker/DO code、native API、per-DO operation ordering | workerd                              |
| stable DO attachment、facet 控制权限交接              | workerd trusted native owner         |
| process/container creation与host isolation            | Broker/runtime                       |
| application process与其临时 filesystem                | application container                |
| outbound mediation                                    | workerd + egress interceptor sidecar |

SQLite 建议记录可恢复 lease，而不是把 Docker memory/list 当 authority：

```text
container_instance_leases
  application_id, object_id_digest, object_generation,
  target_generation, opaque_engine_key, provider_contract_sha256,
  state, lease_generation, created_at, connected_at,
  stopping_at, stopped_at, lost_at, last_observed_at

container_rollouts
  application_id, from_generation, to_generation,
  kind, steps, current_step, state,
  created_at, started_at, completed_at, failed_at
```

状态机：

```text
admitting -> starting -> running -> stopping -> stopped
admitting/starting/running/stopping -> lost
lost -> starting/running/stopping  # only after proving the same lease/start identity
lost -> stopped                   # only after confirming termination and cleanup
```

规则：

- SQLite transaction 内只做 authority state change；image/Broker/process I/O 在 transaction 外；
- create 使用 lease generation 和 opaque engine key 幂等；未知已存在实例不按名字认领；
- workerd reconnect 时同时验证 start identity、image digest、provider contract 和 process/container状态；
- application main process exit 驱动 `monitor()`，不能因 sidecar/daemon状态伪造正常 exit；
- `destroy()`、重复 stop、DO eviction、client disconnect 与 `ocd` shutdown 行为逐项固定；
- `ocd` restart 后从 SQLite target/leases 与 Broker inventory reconciliation；匹配的实例重连，不匹配的已知实例经身份核对后停止；
  重连到仍有效的旧 effective target 不要求其镜像等于 application 最新 target；
- Broker/runtime restart 后无法证明身份或状态的实例标 `lost`，保留其容量和 DO identity 占用，不把新进程冒充旧 Container；
- `lost` 表示观察不确定，不表示进程退出。证明仍是同一次运行后可恢复相应状态，但不复活旧 facet capability 或已结束的 monitor；
  只有确认该 lease 未创建进程或进程已退出、所需 sidecar/资源清理完成且迟到 create/start 已被 fence 后，才能进入 `stopped` 并释放占用；
- 单次超时、失联或暂时查不到实例不构成终止证据；create 结果未知时先按同一 lease 幂等核对，不能释放后再创建替代实例；
- provider orphan 由 keyed label + authority record识别并 bounded reap；未知 operator container 不触碰；
- recovery 失败不删除 retained failure evidence，不自修复数据库或 image authority。

## 11. Rollout 与 rollback

本节实现 application-wide rollout，Day 1 限定为 `default` scheduling policy；`durable_object` policy 的按实例选镜像合同不在本节范围。
依据 [官方 rollout 合同](https://developers.cloudflare.com/containers/configuration/rollouts/)（CT0 待固定证据），Worker 更新和 Container replacement
是独立生命周期，新 Worker 可访问仍有效的旧镜像实例，rollout 不修改 DO storage。

目标 rollout sequence 如下；CT0 必须对照当前 cf/Cloudflare 的可观察顺序确认或修订，不能把旧 producer 顺序当作已资格合同：

```text
activate Worker version
  -> admit/materialize image and target
  -> create rollout if effective Container config changed
```

Worker code可能在 rollout 完成前访问旧 image instance。P23 因此必须持久化：

- Worker deployment active version；
- Container target generation；
- 每个 running instance 的 effective generation/image；
- rollout step 与 terminal state。

例如 `route=10 / lease=7 / image=A` 发布新 Worker 与目标 B 后，可变为 `route=11 / lease=7 / image=A / target=B`。
route 更新按第 7.4 节交接控制权限，lease 7 保持有效；只有 rollout 选中该实例并确认 lease 7 终止后，才能分配新 lease 启动 B。
target 更新、Worker 回滚或 facet reload 均不隐式销毁未被选中的实例；new start 的 target 选择只由 `ocd` authority 决定，
不能从当前 Worker snapshot 猜测，也不能在不可用时回退旧镜像。

单机 rollout：

- `max_instances < 2` 时一个 100% step；其他默认/自定义 steps 由当前 cf producer 与官方 rollout API fixture 固定；
- 每一步只选择本机 eligible instances，不能为了“达到百分比”超过 `max_instances`；
- selected instance 按固定合同核对 DO connection age 与 active grace，再向该 lease 的 main process 发送 `SIGTERM`；
- 按官方固定合同最多等待 15 分钟，之后 `SIGKILL`；
- 确认旧 lease process exit、完成必要 cleanup 后再启动 target image；向对应 lease 的 monitor/package 提供 exit outcome，
  `onStop` 的执行遵循固定 package 合同，不以 tenant callback 必须成功完成作为容量释放或 rollout 前进条件；
- image/app code mixed window 是正式语义，不通过全局 stop-the-world 隐藏；
- rollback 创建指向旧 immutable target 的新 target generation，不复活旧 lease/capability；
- rollout失败保留 Worker已激活这一事实，返回固定 CLI/API 状态，不伪造事务回滚。

未支持的 scheduling policy、affinity、regional placement 等配置默认拒绝；`default` policy 按本节实现。若固定 cf/官方 API 要求 round-trip，
可以保存并返回 `unsupported` 状态，但不能接受后忽略并宣称生效。

## 12. Networking 与 egress

产品支持的访问路径是 `ocd`/Worker/DO，再通过 `getTcpPort()` 访问 Container；产品 API 不提供 Container public endpoint，
也不返回 engine published port、bridge IP 或 localhost port。宿主/provider 网络上直接访问 Container 的场景不纳入隔离保证，
不增加 sidecar/relay 实例鉴权或专用网络隔离；operator 管理发布端口和宿主网络可达范围。此限制须进入 capability/deviation 与 runbook。
通过平台 API/native capability 的跨 account/script/object 授权仍必须验证，不因省略网络直连隔离而放宽。

workerd local implementation 为每个 application container 配一个 `proxy-everything` egress interceptor sidecar，并让应用容器
共享其 network namespace。P23 保留一个 authoritative egress path：

```text
container connect/fetch
  -> egress interceptor sidecar
  -> workerd container egress handling
  -> platform-owned IP capability
     Network(allow = ["network", "local"], deny = ["unix", "unix-abstract"])
```

约束：

- `enableInternet=false` 默认无 general outbound；显式 interception binding 仍按固定 API合同处理；
- `enableInternet=true` 沿用 [当前 outbound 配置](../packages/runtime/config.capnp)：允许公网、私网、loopback、link-local、metadata
  及宿主允许的其他 IP 目的地。地址过滤由 operator 的防火墙、network namespace、容器或 VM 承担，不宣称逐 Container IP 隔离；
- native [`ContainerClient`](../third_party/workerd/src/workerd/server/container-client.c++) 当前直接使用 native network 建立 passthrough
  连接，不能假设 Worker `globalOutbound` 自动约束它。
  实施时将 tenant 出站连接接入上述平台 IP capability；Broker Unix socket 与内部 relay 连接继续使用独立的受信任内部能力；
- `unix`/`unix-abstract` 拒绝只针对 tenant 出站借宿主网络能力访问宿主 Unix endpoint，不禁止 Container 内部自己的 Unix socket IPC。
  不向 application 挂载宿主 Docker/Broker/control socket；workerd 私有连接 Broker 不属于 tenant outbound；
- 不增加 hostname pre-resolution、port ACL、自动防火墙管理、JavaScript 出站代理或第二条 container NAT fallback；
- 平台 control API 和 Broker 仍独立鉴权，不把来源为 loopback 当成授权；Container 数据路径的直连范围按本节产品定位处理；
- outbound HTTP/HTTPS/TCP interception 的 hostname/glob/IP/CIDR匹配、replacement 和 open-connection update 按固定 workerd
  behavior；
- CA injection、HTTPS interception、credential/material 不能写进 application image layer、env、logs 或 exec output；
- sidecar image是 operator/provider正式 dependency，必须 pin digest、离线可用并通过 supply-chain验证；缺失时 fail closed；
- app/sidecar任一 crash、network namespace teardown 和 restart race有独立 regression coverage。

## 13. Limits、admission 与 backpressure

不复制 Cloudflare account plan 数值，也不设置 LynxOS “约 20 人”默认值。operator 必须配置或选择一个明确 provider profile：

- total/per-account/per-application running instances；
- pending starts、concurrent stops/reconcile/materializations；
- CPU、memory、disk、PID/process/open-files；
- image compressed/unpacked/layer/manifest limits；
- start/readiness/idle/max-lifetime/stop deadlines；
- concurrent execs、exec stdin/stdout/stderr bytes、buffer/queue；
- ports、connections、HTTP body、WebSocket frame/message/queue；
- logs/metrics retention 与 Broker inventory bounds。

admission permit 从 `admitting` 持有到确认终止并完成清理的 `stopped`，不是 Worker request 生命周期内的短 semaphore。
`lost` 保留 permit 和 DO identity 占用，禁止同一对象的替代启动；恢复同一 lease 不重复计数。`max_instances` 在生产必须由
SQLite/`ocd` admission 与 Broker effective count 双层执行；不能复制 Miniflare 本地开发中“不应用 `max_instances`”的行为。
Broker 计数包括属于本平台但尚未恢复关联的实例及结果未知的 create/start reservation；无法核实归属或完整计数时，受影响的
application/account/provider 配额范围暂停新 admission，不能把未知项按零处理。operator 的无关 workload 不认领、不删除。
资源终止与清理的 provider I/O 在 transaction 外完成；记录释放确认与解除 DO identity 占用在同一 authority transaction 完成，
迟到旧 lease 的请求不得重新创建进程。

instance type 映射表是 operator deployment capability：

- 固定 Cloudflare name可以作为 compatibility input；
- effective CPU/memory/disk必须能由 Broker实际 enforce；
- provider不支持某资源或上限时 deployment fail closed；
- API/status返回配置值与effective值，不把本机 profile谎报成 Cloudflare entitlement；
- current local Docker client 未证明完整执行 custom instance resources，因此 Broker Gate 必须观测 cgroup/runtime effective state，
  不能只检查 request JSON。

## 14. Containers public API 与 cf commands

CT0 从固定 cf source/OpenAPI 抽取唯一 route inventory。2026-10-05 的本地 `cf cli search` 已确认
`cf containers applications create/edit/list`、`cf containers applications rollouts create` 与
`cf containers images prepare` 的入口；这只证明命令存在，不证明当前 `ocd` 支持对应 API。
进一步使用命令 help 与 `cf schema <discovered-command>` 冻结 method/query/body，不猜测旧命令映射。目标 family 包含：

```text
/client/v4/accounts/{account_id}/containers/applications/**
application rollouts
instances
images and registries
selected command/SSH surfaces
```

不能根据 route 名称猜 schema。每个选定 command/route 单独固定：method、query、request/response、v4 envelope、pagination、
error、polling、terminal status 与 CLI exit code。

Day 1 优先级：

1. `cf deploy` 实际调用的 application create/update、image、rollout；
2. list/status/delete 与安全 operator diagnosis；
3. image list/delete/prune 中可由 immutable authority 安全表达的部分；
4. instance list/status；
5. exec/SSH 只有独立 auth、audit、stream/terminal resize、timeout 和 Broker capability通过后开放。

SSH/interactive exec 不是实现 Worker `ctx.container.exec()` 的前置条件。未支持命令返回明确标准失败，不提供把 Docker socket、
host shell 或 provider CLI 暴露给用户的替代入口。

## 15. Error、observability 与 secret handling

内部稳定 error classes：

```text
invalid_config, unsupported, image_not_found, image_rejected,
image_materialization_failed, capacity_exhausted, start_timeout,
provider_unavailable, provider_contract_mismatch, container_lost,
container_not_running, exec_rejected, exec_failed, port_unavailable,
egress_denied, rollout_failed, malformed_provider_response
```

Worker exception type/message、public API code/message/status 与 cf exit code由固定 fixture分别映射，不能直接暴露 Broker/
Docker error。retryability、`Retry-After`、monitor rejection 和 signal/exit semantics 有明确表，不能用 provider message regex 猜。

日志/metrics 推荐低基数维度：

```text
account_id, script_id, application_id, operation,
target_generation, provider_contract_sha256,
instance_profile, result_class,
queue_wait_ms, start_ms, duration_ms, bytes_in, bytes_out
```

禁止记录：

- env/secrets、exec stdin/stdout/stderr/body；
- image registry credential、signed URL、Broker auth/socket、raw Docker response；
- raw DO ID、container ID、engine name、mount path、host PID/IP/topology；
- HTTP authorization/cookie、intercepted TLS material、Container traffic body。

P7 tail只显示 Worker触发的 Container operation metadata/outcome；operator diagnosis可以用 opaque ID关联受保护的 Broker审计，
不能把 host细节送进 tenant tail。

## 16. Miniflare、workerd 与 WDL 参考边界

### 16.1 Miniflare

Miniflare 只提供开发/runtime 行为参考；配置、Build Output、upload 与 deploy 顺序须使用当前 pinned cf/官方 Vite 插件证据：

- config normalization、upload metadata 与 application/rollout call order；
- Container metadata关联到 DO namespace；
- `containerEngine.localDocker` socket discovery/config；
- cf 开发工具链的 image build/pull/tag（CT0 待固定实际调用）；
- workerd按 Worker call启动本地实例；
- safe environment 下的 FUSE privilege detection；
- dev session teardown与hot rebuild行为。

明确不复制：

- raw Docker socket作为 production boundary；
- developer Docker context/PATH discovery进入 `ocd` startup；
- 首次 Worker request build/pull image；
- local dev 不执行 `max_instances`；
  -自动授予 FUSE、device、CAP_SYS_ADMIN 或 rootful Docker privilege；
- Miniflare临时 tag/cache作为 image authority；
- dev-only cleanup/error/retry 作为 production lifecycle。

### 16.2 workerd

复用 current fork 的 native Container API、RPC、Docker client、exec streams、port tunnel、monitor、egress interception 与 reconnect
代码。新增内容限于：

- dynamic loaded DO class 的 Container attachment/policy、稳定 owner 与 facet 控制权限交接；
- 将 native tenant passthrough 接入既有平台 IP capability，保留受信任内部 Unix/relay 连接；
- platform-owned identity/resource labels；
- Broker所需的严格 provider metadata；
- compatibility/security bug fix及其 upstream-style tests。

不在 Rust/TypeScript 中重写完整 container engine；长期只集成通过 G0 的现成 embeddable runtime并实现固定 Docker 子集适配。
fork变更在 `third_party/workerd/` 独立提交，再由协调 pin、archive/digest、compatibility date/flags和正式 real-runtime Gate更新
主仓库 gitlink与 runtime lock。

### 16.3 WDL

[WDL](https://github.com/wdl-dev/wdl) 当前没有 `ctx.container`/Cloudflare Containers compatibility implementation。它使用
容器部署自身平台组件，不作为 P23 provider或API authority。后续发现的 WDL功能只有通过同一官方合同/测试后才可引用，不能因
项目也是 self-hosted Workers platform 就推断兼容。

## 17. 实施顺序

### CT0：冻结合同

- 固定 cf/config、mode、Build Output、upload metadata、deploy/container command call graph；
- 固定 Workers types、`@cloudflare/containers` package tarball/integrity/source；
- 固定 `ctx.container` JSG descriptors、exceptions、streams、Fetcher/Socket/monitor behavior；
- 固定 public API/OpenAPI、rollout状态、CLI polling/exit与remote traces；
- 建 config/field/route/API/error/compatibility-flag/capability inventory；
- 把 Worker API compatibility 与 single-machine fleet deviations 分开登记。

### CT-G0：动态 Container feasibility Gate

- 使用当前 fork与一个 operator-installed Docker engine进行 disposable probe；
- static `DoHost` 中只给一个声明 Container 的动态 tenant class挂载 native `ctx.container`；
- 固定 `@cloudflare/containers` constructor/start/readiness/fetch/WebSocket/stop通过；
- low-level start/exec streams/monitor/getTcpPort/outbound intercept通过；
- 两个 tenant classes使用不同 immutable image，non-Container class看不到 capability；
- DO eviction、tenant version change、workerd restart与engine process restart不串 identity；
- code-only deploy 保持同一 Container lease/process/filesystem，旧 facet 操作拒绝，新 facet 可接管旧 effective target；
- 两实例分批 rollout 只替换被选中的实例，延迟 cleanup 不命中新 lease，接管失败不释放旧实例容量；
- 记录需要的最小 workerd extension与Docker API trace；probe代码完成后删除，保留结果证据。

Exit：若 native capability 不能安全挂到 dynamic loaded DO/facet，或不能完成上述交接与 lease fencing，P23 保持 unsupported；
不能退回 JS 模拟或把所有 tenant 共用一个静态 image/container capability。

### CT1：workerd dynamic attachment

- 实现 `ContainerClassPolicy` 与dynamic loaded DO attachment；
- stable DO identity、route authority 与 instance lease 分离，facet handover、monitor/stream/interception lifetime 和 cleanup fencing；
- platform image/resource/egress policy注入；
- tenant experimental override拒绝；
- native workerd unit/integration tests与formal fork commit。

### CT2：image/application authority

- P6 metadata decode、current Day 1 SQL/schema/checksum；
- OCI manifest/config/layer validation与artifact authority；
- application/target/rollout/lease repositories，已有 lease 接管与新 start admission 分开；
- `lost` 容量/identity 占用、未知 create/start reservation 和确认终止后的原子释放；
- immutable image digest、rollback pointer与API wire models；
- provider image materialization contract。

### CT3A：短期 Docker + Broker

- operator config、Unix socket validation、capability/version handshake；
- minimal Docker Engine subset validation/proxy，不提供通用 Docker remote API；
- host Docker socket、rootless/rootful profile、预物化 image 与版本资格验证；
- `ocd` 容器化部署中的 sibling container、共享 network namespace 与 loopback relay；
- provider 发布端口和直连 Container 的范围进入部署说明，不增加数据路径防绕过鉴权或隔离；
- image/resource/privilege/network/mount enforcement；
- application + egress sidecar lifecycle、status/reconnect/cleanup；
- provider crash/restart、orphan与corrupt cache recovery；未恢复关联实例保守计数，计数不确定时拒绝新 admission。

### CT3B：长期 embedded runtime + Docker 子集 shim

- BoxLite 与其他可嵌入候选的有界 G0，不在设计期预先冻结 engine；
- 在不修改 workerd wire/API 的前提下实现同一 13/21-family contract；
- app/sidecar shared-network、loopback control、egress、exec upgrade/framing与archive等价；
- snapshot/volume/image ancestry映射，或明确收窄并公开兼容范围；
- `ocd` ownership、offline packaging、目标平台、VMM/helper supervision与crash recovery；
- CT3B 达标后把 embedded provider设为长期默认，不保留两套 authority或重复 lifecycle实现。

### CT4：runtime API 与 official package

- full supported `ctx.container` inventory；
- exec stream/PTY/AbortSignal、Fetcher/Socket/WebSocket；
- monitor/destroy/signal/restart races；
- outbound HTTP/HTTPS/TCP interception；
- fixed official package lifecycle/alarm/schedule/hooks/helpers。

### CT5：cf/control plane/rollout

- standard config/upload/deploy sequence；
- application/image/rollout routes与CLI polling；
- local single-machine capacity与rollout worker；
- list/status/delete/image/instance command subset；
- P12 launcher/target/auth集成与unsupported command behavior。

### CT6：security、operations 与 qualification

- cgroup/runtime effective resource checks、现有 IP outbound/宿主 Unix 拒绝与 Container 内部 IPC regression；
- 平台 API/native capability 的 identity/cross-account/cross-script/cross-version differentials，不包含直接访问 Container 的网络隔离 Gate；
- `lost` 期间禁止替代启动、未知 create 结果恢复、同一 lease 重连与确认终止后释放容量；
- Broker/workerd/`ocd` crash、restart、upgrade、orphan cleanup与soak；
- P7/P9、readiness、metrics、backup（authority metadata only）与runbook；
- fixed cf/package/remote differential；
- capability/deviation/reference/examples/Dashboard同步。

## 18. 必测矩阵

| case                                          | 预期                                                           |
| --------------------------------------------- | -------------------------------------------------------------- |
| `cloudflare.config.ts` containers/exports/env | fixed cf normalization、Build Output与metadata精确通过         |
| Container class未绑定同script SQLite DO       | deploy固定失败，无部分application                              |
| unsupported placement/affinity/privilege      | 明确拒绝，不静默忽略                                           |
| Dockerfile build/push                         | build在cf/developer侧；`ocd` startup/request不build/download   |
| image tag changes after deploy                | runtime仍使用admission时固定digest                             |
| missing/mismatched image                      | rollout/start fail closed，无旧tag fallback                    |
| official package constructor                  | Container-enabled class成功；普通class无`ctx.container`        |
| two tenant classes/images                     | identity/image完全隔离，不受共享`DoHost`影响                   |
| `start()`/already running                     | sync/async和exception与fixture一致                             |
| `exec()` streams/output/combined/PTY          | bytes、EOF、backpressure、single-consume一致                   |
| exec AbortSignal/kill/invalid signal          | process与JS异常正确，无leaked exec                             |
| `getTcpPort().fetch/connect`                  | HTTP/WebSocket/TCP/half-close与native behavior一致             |
| `monitor/destroy/signal`                      | resolve/reject/reason/exit/restart generation一致              |
| official package readiness/sleep/alarm        | restart/eviction后无重复或lost timer                           |
| outbound disabled                             | Container无general Internet                                    |
| outbound enabled                              | 使用现有 IP capability；公网/私网/loopback/link-local/metadata 按宿主策略可达 |
| tenant outbound 到宿主 Unix endpoint          | unix/unix-abstract 拒绝；Docker/Broker/control socket 不挂载给 application |
| Container 内部 Unix socket IPC                | 应用内部 IPC 正常，不受宿主 outbound Unix deny 影响           |
| HTTP/HTTPS/TCP interception                   | matching、replacement、open connection behavior一致            |
| cross-account/script/object ID                | 平台 API/native capability 拒绝越权连接、控制或观察；不声称网络直连隔离 |
| tenant尝试image/privilege/socket override     | native boundary拒绝；Broker无危险request                       |
| max instances/pending starts                  | stable capacity error/Retry-After，无无限队列                  |
| effective CPU/memory/disk/PID                 | provider实际限制与reported profile一致                         |
| app crash/sidecar crash                       | monitor/egress/recovery分别正确收敛                            |
| Broker unavailable/restart                    | readiness degraded；匹配实例reconcile，否则lost                |
| lost 但旧容器仍运行                           | 保留容量和 DO identity 占用，不创建替代实例                    |
| create/start 结果未知或实例尚未恢复关联        | 保留 reservation；计数无法核实时暂停受影响配额范围的新 admission |
| lost 恢复同一 lease                           | 验证 start identity 后恢复，不重复计数、不复活旧 capability/monitor |
| lost 确认终止                                 | 完成 cleanup、fence 迟到 create/start 后原子释放容量和 identity 占用 |
| workerd/`ocd` restart                         | identity、image、lease、monitor不串代                          |
| unknown provider container                    | 不认领、不删除operator其他workload                             |
| `ocd` container + host Docker                 | 只有Broker挂host socket；sibling container与loopback relay通过 |
| Docker socket `:ro`或GID不匹配                | 不误判为安全/可用；preflight明确失败                           |
| embedded provider基础13 family                | lifecycle/exec/archive PUT逐字段、状态码和stream通过           |
| embedded provider缺少snapshot 8 family        | capability明确unsupported，不宣称完整兼容                      |
| Worker code-only deploy / facet handover      | 同一 lease/process/image/filesystem；新 facet 接管，旧 capability 拒绝 |
| handover 加载失败或与 rollout/restart 交错     | 保留有效 lease 与容量，可信重试/reconcile，无重复创建           |
| 旧 lease 的延迟 exec/stop/delete/cleanup      | 只针对原 lease/start identity，不能命中新 lease                |
| facet handover 的 monitor/stream/interception | 新 facet 重新观察；旧 Promise 不迁移、旧 callback 不再被调用；断开按 fixture |
| rollout mixed generations                     | old/new按target/lease可解释，DO storage不变                    |
| rollout 第一批只选择部分实例                  | 未选中实例保持旧 image/lease，新 Worker 仍可访问               |
| rollout SIGTERM/drain/SIGKILL                 | fixed grace/15-minute stop contract通过                        |
| rollout later step fails                      | Worker active事实与rollout失败状态保留                         |
| rollback                                      | 新target generation指向旧digest，不复活旧capability            |
| raw Docker/provider error                     | Worker/API/log均为sanitized stable mapping                     |
| cf deploy与选定Containers commands            | route、envelope、polling、exit code与fixed CLI一致             |
| unsupported cf SSH/command                    | 明确失败，不暴露host shell/socket                              |

## 19. Definition of Done

P23 只有同时满足以下条件才可归档：

- 固定 cf 的 Container config/mode、Build Output、upload、application/image/rollout 序列对真实 `ocd` 通过；
- 固定 Workers types和`@cloudflare/containers`在正式 pinned workerd中直接运行，无custom package/import rewrite；
- supported `ctx.container` API的descriptor、exception、stream、Fetcher/Socket、monitor与compatibility flags逐项通过；
- dynamic `DoHost` 只为声明class附加native capability，不共享静态image或泄露给non-Container class；
- stable attachment 与 facet 控制权限分离，code-only deploy 不重启 Container；旧 effective target 可接管，旧调用/cleanup 不串 lease；
- immutable image digest、application target、instance lease、capacity与rollout由`ocd`/SQLite authority持有；
- `lost` 和未知 create/start 保留容量/identity 占用，Broker 保守计数；仅确认终止、cleanup 和迟到操作 fencing 后释放；
- 短期 Broker/Docker是operator prerequisite，不被open-compute下载、打包、启动、supervise或公开；
- 长期 embedded runtime通过候选G0、同一Docker子集合同、离线打包和`ocd` lifecycle/recovery Gate后，才可替代短期依赖；
- workerd不持有raw Docker socket；短期只有Broker持有且不向上暴露通用权限，dangerous HostConfig/mount/device/capability全部fail closed；
- CPU/memory/disk/PID/instance/network limits经过provider effective-state测试，不只验证配置；
- Container native 出站沿用平台 IP capability，宿主 Unix endpoint 不可经 tenant outbound 访问，Container 内部 Unix IPC 可用；
- image chain/digest/materialization/cache corruption与offline runtime startup通过；
- app/sidecar/Broker/workerd/`ocd` crash、restart、eviction、rollout、rollback、orphan cleanup和soak通过；
- single-machine placement/isolation/rollout deviations进入compatibility matrix、capability manifest和用户文档；
- 明确不保证阻止宿主/provider 网络直连 Container，不加入该场景的额外鉴权/隔离 Gate；管理 API/native capability 授权与 engine socket 保护照常验收；
- WDL/Miniflare只作为固定source evidence，不成为production authority或fallback；
- Cloudflare remote differential完成，或credential/availability限制拆成独立active acceptance；
- P6/P7/P9/P20、references、examples、runbook和Dashboard同步；
- 正式release仍以单个`ocd` executable + 既有单个pinned workerd child为目标；短期外部Docker/Broker与长期嵌入式runtime边界分别明确。

文档变更本身只运行`git diff --check`、Markdown链接和固定源码/命令核对。实施属于workerd fork、protocol、container/process、
network、security、persistence、artifact和release变更，必须先显式`bun run build`准备runtime assets，再执行仓库`AGENTS.md`
要求的focused tests、coverage与最终单轮workspace Gate。Linux rootful/FUSE、loopback mutation或其他privileged fixture仍需用户明确授权。
